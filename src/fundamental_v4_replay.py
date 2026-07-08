"""V4 pre-registered replay (READ-ONLY analysis). Replays the fundamental+rates
composite at each historical as-of for three variants — V4.0 (std+early = production),
V4.1 (mad+early), V4.2 (mad+late) — set purely via config flags, then joins MT5 OHLC
forward returns to score the pre-registered metrics (C-A..C-D). Nothing is adopted.

Scope note: the replayed composite is fundamental categories + monetary/realyield/liquidity
(FX pairs+singles via build_payload; cross-asset via compute_crossasset_scores). SENTIMENT
and TREND are render-layer and VARIANT-INVARIANT, so they are excluded from the replay — the
variant effect lives entirely in the fundamental pillar.
"""
from __future__ import annotations

import argparse
import copy
import logging
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from src.economic_compute import build_payload
from src.crossasset_compute import compute_crossasset_scores
from src.rate_compute import compute_rate_scores
from src.realyield_compute import compute_realyield_score
from src.liquidity_compute import compute_liquidity_score
from src.ff_scoring import build_matcher, to_scoring_frame

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
FF = ROOT / "data" / "economic_calendar_ff.parquet"
RATES = ROOT / "data" / "rates.parquet"
REALY = ROOT / "data" / "real_yields.parquet"
NETLIQ = ROOT / "data" / "net_liquidity.parquet"
PRICE = ROOT / "data" / "price_history.parquet"
IND_YAML = ROOT / "data" / "economic_indicators.yaml"
INST_YAML = ROOT / "data" / "economic_instruments.yaml"
CA_YAML = ROOT / "data" / "crossasset_instruments.yaml"
CSV_OUT = ROOT / "data" / "fundamental_v4_backfill.csv"

VARIANTS = {"V4.0": ("std", "early"), "V4.1": ("mad", "early"), "V4.2": ("mad", "late")}
HORIZONS = [5, 10]


def _variant_cfg(base: dict, sigma_method: str, quantize: str) -> dict:
    d = copy.deepcopy(base)
    d["defaults"]["sigma_method"] = sigma_method
    d["defaults"]["quantize"] = quantize
    return d


def run_replay(step: int = 1) -> pd.DataFrame:
    cal = to_scoring_frame(pd.read_parquet(FF), build_matcher())   # gated (zero-quarantine)
    cal["release_dt"] = pd.to_datetime(cal["release_dt"])
    rates = pd.read_parquet(RATES); rates["date"] = pd.to_datetime(rates["date"])
    realy = pd.read_parquet(REALY) if REALY.exists() else pd.DataFrame()
    netliq = pd.read_parquet(NETLIQ) if NETLIQ.exists() else pd.DataFrame()
    ind_base = yaml.safe_load(open(IND_YAML)); inst = yaml.safe_load(open(INST_YAML))
    ca_cfg = yaml.safe_load(open(CA_YAML))
    ind_cfgs = {v: _variant_cfg(ind_base, *VARIANTS[v]) for v in VARIANTS}

    cal_actual = cal[cal["actual"].notna()]
    last = cal_actual["release_dt"].max().normalize()
    first = (cal["release_dt"].min() + pd.Timedelta(days=120)).normalize()
    asofs = pd.bdate_range(first, last)[::step]

    rows = []
    for as_of in asofs:
        cal_slice = cal[cal["release_dt"] <= as_of]
        rs = compute_rate_scores(rates[rates["date"] <= as_of], as_of=as_of.date()) if len(rates) else {}
        ry = None
        if len(realy):
            r = compute_realyield_score(realy[pd.to_datetime(realy["date"]) <= as_of], as_of=as_of.date())
            ry = r if r is not None else None
        ls = None
        if len(netliq):
            l = compute_liquidity_score(netliq[pd.to_datetime(netliq["date"]) <= as_of], as_of=as_of.date())
            ls = l if l is not None else None
        for v, ind_cfg in ind_cfgs.items():
            payload = build_payload(cal_slice, ind_cfg, inst, as_of=as_of, rate_scores=rs or None)
            for ip in payload["instruments"]:
                rows.append({"as_of": as_of, "variant": v, "symbol": ip["symbol"],
                             "cls": "fx" if ip["type"] == "fx" else "single",
                             "score": ip["score"], "bias": ip["bias"]})
            cats = {ccy: card.get("categories", {}) for ccy, card in payload["currencies"].items()}
            ca = compute_crossasset_scores(cats, ry, ca_cfg, liquidity_score=ls)
            for sym, r in ca.items():
                rows.append({"as_of": as_of, "variant": v, "symbol": sym, "cls": "cross-asset",
                             "score": r.get("score_precise", 0.0), "bias": r.get("bias_label")})
    df = pd.DataFrame(rows)
    df["as_of"] = pd.to_datetime(df["as_of"])
    return df, _forward_returns()


def _forward_returns() -> dict:
    """{symbol: DataFrame(date, close)} sorted, for as-of forward-return joins."""
    px = pd.read_parquet(PRICE); px["date"] = pd.to_datetime(px["date"])
    out = {}
    for sym, g in px.groupby("symbol"):
        out[sym] = g.sort_values("date")[["date", "close"]].reset_index(drop=True)
    return out


def attach_returns(df: pd.DataFrame, px: dict) -> pd.DataFrame:
    """r_H = close(as_of + H bd)/close(as_of) − 1, per symbol. as-of and as-of+H bd
    use the last close on/at-or-before each target date (asof merge)."""
    df = df.copy()
    for H in HORIZONS:
        df[f"r{H}"] = np.nan
    for sym, series in px.items():
        m = df["symbol"] == sym
        if not m.any():
            continue
        s = series.set_index("date")["close"]
        idx = df.loc[m, "as_of"]
        c0 = s.reindex(idx, method="ffill").values
        for H in HORIZONS:
            tgt = idx + pd.tseries.offsets.BDay(H)
            cH = s.reindex(tgt, method="ffill").values
            df.loc[m, f"r{H}"] = cH / c0 - 1.0
    return df


def hit_rate(df: pd.DataFrame, H: int) -> pd.DataFrame:
    """Directional hit-rate on non-Neutral obs, per variant × class × sub-period."""
    d = df[(df["bias"] != "Neutral") & df[f"r{H}"].notna() & (df["score"].abs() > 0)].copy()
    d["yr"] = d["as_of"].dt.year
    d["hit"] = (np.sign(d["score"]) == np.sign(d[f"r{H}"])).astype(float)
    rows = []
    for (v, cls), g in d.groupby(["variant", "cls"]):
        for yr in ["all", 2024, 2025, 2026]:
            gg = g if yr == "all" else g[g["yr"] == yr]
            if len(gg) < 20:
                continue
            rows.append({"variant": v, "cls": cls, "period": yr, "n": len(gg),
                         "hit_rate": round(100 * gg["hit"].mean(), 1)})
    return pd.DataFrame(rows)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", type=int, default=1)
    ap.add_argument("--csv", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.ERROR)
    df, px = run_replay(step=args.step)
    df = attach_returns(df, px)
    if args.csv:
        df.to_csv(CSV_OUT, index=False)
        print(f"wrote {CSV_OUT} ({len(df)} rows)")
    # sign sentinel
    e = df[(df.symbol == "EURUSD") & (df.variant == "V4.0") & df.r5.notna() & (df.score.abs() > 0)]
    if len(e):
        corr = np.sign(e.score).corr(np.sign(e.r5))
        print(f"\nSIGN SENTINEL EURUSD (V4.0): sign(score)~sign(r5) corr={corr:+.2f} "
              f"({'OK: score>0 = EURUSD up' if corr > 0 else 'INVERTED'})")
    for H in HORIZONS:
        print(f"\n=== HIT-RATE H={H} (non-Neutral, |score|>0) ===")
        print(hit_rate(df, H).pivot_table(index=["cls", "period"], columns="variant",
              values="hit_rate").to_string())
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
