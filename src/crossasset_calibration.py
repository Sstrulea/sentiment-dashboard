"""Cross-Asset bias-threshold calibration — READ-ONLY analysis.

Backfills the cross-asset instrument scores over the available history and
reports the score/bias distribution under the current placeholder thresholds
(mild 1.3 / very 3.0), then recommends bias_thresholds for
data/crossasset_instruments.yaml. Mirrors src/calibration_analysis.py (FX).

For each as-of day it rebuilds the inputs exactly as the dashboard would:
  - per-currency category scores via build_payload (calendar + rates ≤ as_of);
  - the US real-yield momentum via compute_realyield_score on real_yields.parquet
    ≤ as_of (auto-selects the available series — REAL_10Y_TSY in practice);
then scores the 8 instruments via compute_crossasset_scores.

NOTE: the universe is small and correlated (DJIA/SP500/NASDAQ are identical;
GOLD/SILVER identical) — the report shows both the raw all-8 view and a
de-duplicated by-profile view so percentiles aren't dominated by clones.

    python -m src.crossasset_calibration [--step N]

Writes nothing, changes no config. STOP at report.
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
from src.realyield_compute import compute_realyield_score
from src.liquidity_compute import compute_liquidity_score
from src.crossasset_compute import compute_crossasset_scores

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
CAL = ROOT / "data" / "economic_calendar.parquet"
RATES = ROOT / "data" / "rates.parquet"
REAL_YIELDS = ROOT / "data" / "real_yields.parquet"
NET_LIQUIDITY = ROOT / "data" / "net_liquidity.parquet"
INDICATORS_YAML = ROOT / "data" / "economic_indicators.yaml"
INSTRUMENTS_YAML = ROOT / "data" / "economic_instruments.yaml"
CROSSASSET_YAML = ROOT / "data" / "crossasset_instruments.yaml"

BUCKETS = ["Very Bearish", "Bearish", "Neutral", "Bullish", "Very Bullish"]

# One representative per correlated profile (US indices identical; metals identical).
PROFILE_REP = ["SP500", "DAX", "NIKKEI", "FTSE100", "GOLD"]


# ---------------------------------------------------------------------------
# Backfill
# ---------------------------------------------------------------------------

def run_backfill(step: int = 1, max_asof: int = 300):
    cal = pd.read_parquet(CAL); cal["release_dt"] = pd.to_datetime(cal["release_dt"])
    rates = pd.read_parquet(RATES); rates["date"] = pd.to_datetime(rates["date"])
    ry = pd.read_parquet(REAL_YIELDS); ry["date"] = pd.to_datetime(ry["date"])
    nl = None
    if NET_LIQUIDITY.exists():
        nl = pd.read_parquet(NET_LIQUIDITY); nl["date"] = pd.to_datetime(nl["date"])
    ind = yaml.safe_load(open(INDICATORS_YAML))
    inst = yaml.safe_load(open(INSTRUMENTS_YAML))
    ca_cfg = yaml.safe_load(open(CROSSASSET_YAML))

    # As-of window: business days ending at the latest calendar actual; start
    # ~120d in so sigma has prints. Also require real-yield history to exist.
    cal_actual = cal[cal["actual"].notna()]
    last = cal_actual["release_dt"].max().normalize()
    first = (cal["release_dt"].min() + pd.Timedelta(days=120)).normalize()
    ry_first = ry["date"].min().normalize() + pd.Timedelta(days=1)
    first = max(first, ry_first)
    asofs = pd.bdate_range(first, last)
    if len(asofs) > max_asof:
        asofs = asofs[-max_asof:]
    asofs = asofs[::step]

    rows = []
    ry_chosen_series = set()
    ry_present_days = 0
    for as_of in asofs:
        cal_slice = cal[cal["release_dt"] <= as_of]
        rates_slice = rates[rates["date"] <= as_of]
        rs = compute_rate_scores(rates_slice, as_of=as_of.date()) if not rates_slice.empty else {}
        payload = build_payload(cal_slice, ind, inst, as_of=as_of, rate_scores=rs or None)
        categories_by_ccy = {c: card.get("categories", {}) for c, card in payload["currencies"].items()}

        ry_slice = ry[ry["date"] <= as_of]
        rys = compute_realyield_score(ry_slice, as_of=as_of.date()) if len(ry_slice) else None
        if rys is not None:
            ry_present_days += 1
            ry_chosen_series.add(rys.series)

        ls = None
        if nl is not None:
            nl_slice = nl[nl["date"] <= as_of]
            ls = compute_liquidity_score(nl_slice, as_of=as_of.date()) if len(nl_slice) else None

        scores = compute_crossasset_scores(categories_by_ccy, rys, ca_cfg, liquidity_score=ls)
        for sym, r in scores.items():
            rows.append({
                "as_of": as_of.date(), "symbol": sym, "type": r["type"],
                "score_precise": r["score_precise"], "bias": r["bias_label"],
                "ry_present": rys is not None,
            })

    meta = {
        "window": (asofs[0].date(), asofs[-1].date(), len(asofs)),
        "ry_present_days": ry_present_days,
        "ry_series": sorted(ry_chosen_series),
        "scale": ca_cfg.get("scale"),
        "thresholds": ca_cfg.get("bias_thresholds", {}),
    }
    return pd.DataFrame(rows), meta


# ---------------------------------------------------------------------------
# Analysis helpers
# ---------------------------------------------------------------------------

def _pctiles(s: pd.Series) -> dict:
    s = s.dropna()
    if s.empty:
        return {}
    return {"min": s.min(), "p50": s.quantile(.50), "p75": s.quantile(.75),
            "p90": s.quantile(.90), "p95": s.quantile(.95), "max": s.max()}


def _bias_split(biases: pd.Series) -> dict:
    n = len(biases)
    if not n:
        return {}
    vc = biases.value_counts()
    pct = {b: 100.0 * vc.get(b, 0) / n for b in BUCKETS}
    pct["_neutral"] = pct["Neutral"]
    pct["_directional"] = pct["Bearish"] + pct["Bullish"]
    pct["_very"] = pct["Very Bearish"] + pct["Very Bullish"]
    return pct


def _relabel(df: pd.DataFrame, mild: float, very: float) -> pd.Series:
    th = {"mild": mild, "very": very}
    return df["score_precise"].apply(lambda s: bias_label(s, th))


def _print_split(title: str, biases: pd.Series) -> None:
    p = _bias_split(biases)
    if not p:
        print(f"  {title}: (no data)")
        return
    print(f"  {title} (n={len(biases)}): "
          f"Neutral {p['_neutral']:.0f}% · directional {p['_directional']:.0f}% · very {p['_very']:.0f}%")
    print("     " + "  ".join(f"{b}={p[b]:.0f}%" for b in BUCKETS))


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def analyze(df: pd.DataFrame, meta: dict) -> None:
    w = meta["window"]
    print("\n" + "=" * 92)
    print(f"CROSS-ASSET CALIBRATION — as-of {w[0]} → {w[1]} ({w[2]} days × 8 instruments = {len(df)} obs)")
    print(f"real_yield present on {meta['ry_present_days']}/{w[2]} days; series used: {meta['ry_series'] or '—'}")
    print(f"current placeholder thresholds: {meta['thresholds']} · scale {meta['scale']}")
    print("=" * 92)

    df = df.copy()
    df["abs"] = df["score_precise"].abs()
    dedup = df[df["symbol"].isin(PROFILE_REP)]   # one rep per correlated profile

    print("\n[A] |score| DISTRIBUTION")
    for label, sub in [("ALL 8", df), ("by-profile (5 reps)", dedup),
                       ("indices", df[df.type == "index"]), ("metals", df[df.type == "metal"])]:
        p = _pctiles(sub["abs"])
        if p:
            print(f"  {label:22} " + " ".join(f"{k}={v:.2f}" for k, v in p.items()))

    print("\n[B] BIAS SPLIT under CURRENT thresholds (mild=%(mild)s, very=%(very)s)" % meta["thresholds"])
    _print_split("ALL 8", df["bias"])
    _print_split("by-profile", dedup["bias"])
    _print_split("indices", df[df.type == "index"]["bias"])
    _print_split("metals", df[df.type == "metal"]["bias"])

    # ---- recommendation ----
    print("\n" + "=" * 92)
    print("RECOMMENDATION (review only — not applied)")
    print("=" * 92)
    # Base thresholds on the by-profile |score| distribution (clone-robust):
    # mild ≈ p55 (≈55% Neutral), very ≈ p90 (≈10% Very). Round to .1.
    a = dedup["abs"]
    mild = round(float(a.quantile(0.55)), 1)
    very = round(float(a.quantile(0.90)), 1)
    # guard ordering / non-degenerate
    if very <= mild:
        very = round(mild + 1.0, 1)
    scale = meta["scale"]
    print(f"  basis: by-profile |score| → mild≈p55={a.quantile(.55):.2f}, very≈p90={a.quantile(.90):.2f}")
    print(f"  recommended: scale {scale} (unchanged) · mild {mild} · very {very}")

    print("\n  PROJECTED bias split under recommended:")
    proj_all = _relabel(df, mild, very)
    proj_prof = _relabel(dedup, mild, very)
    _print_split("ALL 8", proj_all)
    _print_split("by-profile", proj_prof)
    _print_split("indices", _relabel(df[df.type == "index"], mild, very))
    _print_split("metals", _relabel(df[df.type == "metal"], mild, very))

    print(f"\n  PARAMETER TABLE")
    print(f"  {'param':10}{'current':>10}{'recommended':>14}")
    cur = meta["thresholds"]
    print(f"  {'scale':10}{str(scale):>10}{str(scale):>14}")
    print(f"  {'mild':10}{str(cur.get('mild')):>10}{mild:>14}")
    print(f"  {'very':10}{str(cur.get('very')):>10}{very:>14}")
    return mild, very, scale


def show_today(mild: float, very: float) -> None:
    """The 8 instruments as of the latest data, under the recommended thresholds."""
    cal = pd.read_parquet(CAL); cal["release_dt"] = pd.to_datetime(cal["release_dt"])
    ind = yaml.safe_load(open(INDICATORS_YAML)); inst = yaml.safe_load(open(INSTRUMENTS_YAML))
    ca_cfg = yaml.safe_load(open(CROSSASSET_YAML))
    as_of = pd.Timestamp.utcnow().tz_localize(None)
    rs = {}
    if RATES.exists():
        rs = compute_rate_scores(pd.read_parquet(RATES), as_of=as_of.date())
    payload = build_payload(cal, ind, inst, as_of=as_of, rate_scores=rs or None)
    cats = {c: card.get("categories", {}) for c, card in payload["currencies"].items()}
    rys = compute_realyield_score(pd.read_parquet(REAL_YIELDS), as_of=as_of.date()) if REAL_YIELDS.exists() else None
    ls = compute_liquidity_score(pd.read_parquet(NET_LIQUIDITY), as_of=as_of.date()) if NET_LIQUIDITY.exists() else None
    scores = compute_crossasset_scores(cats, rys, ca_cfg, liquidity_score=ls)

    th = {"mild": mild, "very": very}
    print("\n  TODAY under recommended thresholds (real_yield series: "
          f"{rys.series if rys else '—'}, score {rys.score if rys else '—'}):")
    print(f"  {'SYMBOL':10}{'score':>8}  {'current bias':16}{'recommended bias'}")
    for sym, r in sorted(scores.items(), key=lambda kv: -kv[1]["score_precise"]):
        cur_b = r["bias_label"]
        rec_b = bias_label(r["score_precise"], th)
        mark = "" if cur_b == rec_b else "  <-"
        print(f"  {sym:10}{r['score_precise']:>+8.2f}  {cur_b:16}{rec_b}{mark}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Read-only cross-asset threshold calibration.")
    ap.add_argument("--step", type=int, default=1)
    ap.add_argument("--max-asof", type=int, default=300)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)

    df, meta = run_backfill(step=args.step, max_asof=args.max_asof)
    mild, very, _ = analyze(df, meta)
    show_today(mild, very)
    print("\n[STOP] Analysis only — no config/scoring changed.")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
