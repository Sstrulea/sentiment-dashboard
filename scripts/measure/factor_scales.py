"""feat/factor-scales, Step 0 — measure the factor scales (instrumentation only,
never imported by production).

Weekly reconstruction (Fridays 21:00 UTC, 2023-09-22 .. the last Friday) through
the PRODUCTION functions:
  calendar   scoring_view(_with_policy_rate(_load_calendar_frame(now), decisions, t)),
             cut at release_dt <= t (as scripts/measure/reconstruct.py)
  rates      compute_rate_scores / compute_pair_spread_scores(as_of=t.date())
  COT        score_currencies / score_metals on the reports <= t; a cell counts
             only when its spec_extreme_6m exists
  P/C        compute_pc_metrics(pc_history <= t) -> pc_index_score
  real yield compute_realyield_score(as_of=t.date())  (+ liquidity, weight 0)
TREND stays off (config/pipeline.yaml), as in production.

Per board it records the factor INPUTS, signed for the instrument:
  FX pairs   category = score_precise(base) - score_precise(quote) on the D1=D
             intersection; monetary = clip(z_spread/0.87, -2, 2) when monetary is
             in the intersection; sentiment = COT(base) - COT(quote), USD = 0
  US Dollar  USD categories; monetary = clip(z_2Y_USD/0.87, -2, 2); DXY COT
  indices /  macro = sign x score_precise(home); rates = mean of the present,
  metals     non-stale components, each sign x clip(z/0.87, -2, 2) (home 2Y,
             DFII10); sentiment = COT / P-C cell
sigma = std (ddof=1) of each input over every instrument-week of the window.

Influence of a factor = mean over instrument-weeks of |contribution| divided by
the sum of those means over the board's factors (macro = growth + inflation +
labour), for the v1 formula (production) and the v2 rule (N-c).

Usage:
  python scripts/measure/factor_scales.py validate [--indicators PATH]
  python scripts/measure/factor_scales.py measure  [--indicators PATH] [--out DIR]
`validate` rebuilds the as_of of public/data/economic.json and must reproduce
every FX and cross-asset score (< 1e-9) through BOTH the production path and
this script's own v1 decomposition.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src import economic_render as er  # noqa: E402
from src.cot_score import (EXT_6M_COL, DATE_COL, SYMBOL_COL, load_currencies_history,  # noqa: E402
                           load_metals_history, score_currencies, score_metals)
from src.crossasset_compute import categories_by_currency, compute_crossasset_scores  # noqa: E402
from src.economic_compute import bias_label, build_payload  # noqa: E402
from src.ff_scoring import scoring_view  # noqa: E402
from src.liquidity_compute import compute_liquidity_score  # noqa: E402
from src.rate_compute import compute_pair_spread_scores, compute_rate_scores  # noqa: E402
from src.realyield_compute import compute_realyield_score  # noqa: E402
from src.sentiment_compute import compute_pc_metrics, pc_index_score  # noqa: E402

log = logging.getLogger("factor_scales")

START = pd.Timestamp("2023-09-22 21:00")
RATE_UNIT, RATE_CAP = 0.87, 2.0
CLIP = 3.0
CATS = ("growth", "inflation", "labour")
BOARDS = ("fx_pairs", "us_dollar", "index", "metal")
FACTORS = {"fx_pairs": CATS + ("monetary", "sentiment"),
           "us_dollar": CATS + ("monetary", "sentiment"),
           "index": CATS + ("rates", "sentiment"),
           "metal": CATS + ("rates", "sentiment")}
CACHE = ROOT / "scripts" / "measure" / "__pycache__" / "factor_scales_weeks.pkl"


def fridays(last: pd.Timestamp) -> list[pd.Timestamp]:
    out, t = [], START
    while t <= last:
        out.append(t)
        t += pd.Timedelta(days=7)
    return out


def rate_signal(z, fallback_score) -> float | None:
    """clip(z/0.87, -2, 2); a series without a z (fallback/insufficient method)
    keeps its bucketed score, which already lives on the same ±2 scale."""
    if z is not None and not (isinstance(z, float) and math.isnan(z)):
        return float(max(-RATE_CAP, min(RATE_CAP, z / RATE_UNIT)))
    return None if fallback_score is None else float(fallback_score)


# ---------------------------------------------------------------------------
# Inputs (loaded once)
# ---------------------------------------------------------------------------

class Inputs:
    def __init__(self, indicators_yaml: Path, now: pd.Timestamp):
        self.ind = yaml.safe_load(Path(indicators_yaml).read_text())
        self.inst = yaml.safe_load(er.INSTRUMENTS_YAML.read_text())
        self.xcfg = yaml.safe_load(er.CROSSASSET_YAML.read_text())
        self.cal = er._load_calendar_frame(now)
        self.decisions = er._policy_rate_decisions()
        self.rates = pd.read_parquet(er.RATES_PARQUET)
        self.ry = pd.read_parquet(er.REAL_YIELDS_PARQUET)
        self.liq = pd.read_parquet(er.NET_LIQUIDITY_PARQUET)
        self.cot_fx = load_currencies_history()
        self.cot_metal = load_metals_history()
        self.pc = pd.read_parquet(er.PC_HISTORY_PARQUET)
        self.fx_pairs = [(s, c["base"], c["quote"]) for s, c in self.inst["instruments"].items()
                         if c.get("type") == "fx"]


def _cot_cells(scored: pd.DataFrame, hist: pd.DataFrame, t: pd.Timestamp) -> dict:
    """{symbol: cell} — a cell counts only when its latest spec_extreme_6m exists."""
    out = {}
    for _, row in scored.iterrows():
        g = hist[hist[SYMBOL_COL] == row["symbol"]].sort_values(DATE_COL)
        if len(g) and pd.notna(g[EXT_6M_COL].iloc[-1]):
            out[row["symbol"]] = int(row["cell"])
    return out


def state_at(inp: Inputs, t: pd.Timestamp) -> dict:
    """Everything production would compute at `t` (trend off)."""
    d = t.date()
    cal = er._with_policy_rate(inp.cal, inp.decisions, t)
    cal = scoring_view(cal)
    cal = cal[pd.to_datetime(cal["release_dt"]) <= t]
    rates = inp.rates[pd.to_datetime(inp.rates["date"]) <= pd.Timestamp(d)]
    rate_scores = compute_rate_scores(rates, as_of=d)
    pair_mon = compute_pair_spread_scores(rates, inp.fx_pairs, as_of=d)

    fx_hist = inp.cot_fx[pd.to_datetime(inp.cot_fx[DATE_COL]) <= t]
    fx_cells = _cot_cells(score_currencies(fx_hist), fx_hist, t)
    payload = build_payload(cal, inp.ind, inp.inst, as_of=t, rate_scores=rate_scores or None,
                            sentiment_cells=fx_cells or None, trend_cells=None,
                            pair_monetary=pair_mon or None)

    ry = compute_realyield_score(inp.ry[pd.to_datetime(inp.ry["date"]) <= pd.Timestamp(d)], as_of=d)
    liq = compute_liquidity_score(inp.liq[pd.to_datetime(inp.liq["date"]) <= pd.Timestamp(d)], as_of=d)
    m_hist = inp.cot_metal[pd.to_datetime(inp.cot_metal[DATE_COL]) <= t]
    sent = dict(_cot_cells(score_metals(m_hist), m_hist, t))
    pc = inp.pc[pd.to_datetime(inp.pc["date"]) <= pd.Timestamp(d)]
    pc_cell = None
    try:
        pct = compute_pc_metrics(pc, str(er.PC_THRESHOLDS_YAML))["equity"]["current"]["percentile_rank"]
        if pct is not None:
            pc_cell = int(pc_index_score(pct)[0])
    except Exception:  # noqa: BLE001 — not enough history yet
        pc_cell = None
    if pc_cell is not None:
        for sym, c in inp.xcfg["instruments"].items():
            if (c.get("type") == "index" and c.get("home_ccy") == "USD") or \
                    c.get("sentiment_proxy") == "us_equity_pc":
                sent[sym] = pc_cell
    cats = categories_by_currency(payload["currencies"], inp.xcfg)
    cross = compute_crossasset_scores(cats, ry, inp.xcfg, liquidity_score=liq,
                                      sentiment_by_symbol=sent, trend_by_symbol=None)
    return {"t": t, "payload": payload, "cross": cross, "rate_scores": rate_scores,
            "pair_mon": pair_mon, "fx_cells": fx_cells, "ry": ry, "sent": sent}


# ---------------------------------------------------------------------------
# Factor inputs per board (v1 values and v2 inputs)
# ---------------------------------------------------------------------------

def _present(card: dict) -> dict:
    return {c: v for c, v in card["categories"].items() if (v.get("coverage") or 0) > 0}


def _mon_signal(st: dict, ccy: str):
    rs = st["rate_scores"].get(ccy)
    return None if rs is None else rate_signal(rs.z, rs.rate_score)


def fx_rows(st: dict, inp: Inputs) -> list[dict]:
    """One row per FX instrument: v1 factor values (as production combines them)
    and v2 inputs, plus the weights/structure needed to rebuild the score."""
    cards = st["payload"]["currencies"]
    sentiment_on = bool(st["fx_cells"])
    w_s = float(inp.inst.get("d2c_sentiment_w_s", 0.125))
    rows = []
    for sym, c in inp.inst["instruments"].items():
        if c["type"] == "single":
            card = cards[c["currency"]]
            pres = _present(card)
            v1, v2, w = {}, {}, {}
            for cat, cell in pres.items():
                v1[cat] = float(cell["score_precise"])
                v2[cat] = (_mon_signal(st, "USD") if cat == "monetary" else float(cell["score_precise"]))
                w[cat] = float(cell.get("weight", 1.0))
            s = st["fx_cells"].get("DXY") if sentiment_on else None
            if s is not None:
                v1["sentiment"] = v2["sentiment"] = float(s)
                w["sentiment"] = float(inp.inst.get("sentiment_weight", 0.5))
            rows.append({"symbol": sym, "board": "us_dollar", "sign": float(c.get("sign", 1)),
                         "v1": v1, "v2": v2, "w": w})
            continue
        b, q = cards[c["base"]], cards[c["quote"]]
        inter = set(_present(b)) & set(_present(q))
        v1, v2, w = {}, {}, {}
        pm = st["pair_mon"].get(sym)
        for cat in inter:
            w[cat] = float(b["categories"][cat].get("weight", 1.0))
            if cat == "monetary":
                if pm is not None:
                    v1[cat] = float(pm["m"])
                    v2[cat] = rate_signal(pm["z"], pm["m"])
                else:
                    v1[cat] = float(b["categories"][cat]["score_precise"] - q["categories"][cat]["score_precise"])
                    v2[cat] = _mon_signal(st, c["base"]) - _mon_signal(st, c["quote"])
            else:
                v1[cat] = v2[cat] = float(b["categories"][cat]["score_precise"]
                                          - q["categories"][cat]["score_precise"])
        if sentiment_on:
            sb = 0 if c["base"] == "USD" else st["fx_cells"].get(c["base"], 0)
            sq = 0 if c["quote"] == "USD" else st["fx_cells"].get(c["quote"], 0)
            v1["sentiment"] = v2["sentiment"] = float(sb - sq)
            w["sentiment"] = w_s
        rows.append({"symbol": sym, "board": "fx_pairs", "sign": 1.0, "v1": v1, "v2": v2, "w": w})
    return rows


def cross_rows(st: dict, inp: Inputs) -> list[dict]:
    cards = st["payload"]["currencies"]
    ry = st["ry"]
    rows = []
    for sym, c in inp.xcfg["instruments"].items():
        home = c["home_ccy"]
        card = cards.get(home, {"categories": {}})
        pres = _present(card)
        v1, v2, w = {}, {}, {}
        for name, fc in c["factors"].items():
            weight = float(fc.get("weight", 1.0))
            if name in CATS:
                if name in pres:
                    sgn = float(fc.get("sign", 1))
                    v1[name] = sgn * int(pres[name]["score_cell"])
                    v2[name] = sgn * float(pres[name]["score_precise"])
                    w[name] = weight
            elif name == "rates":
                n1 = n2 = ws = 0.0
                for comp, cc in fc["components"].items():
                    cw, sgn = float(cc.get("weight", 1.0)), float(cc.get("sign", 1))
                    if cw <= 0:
                        continue
                    if comp == "rate_exp_2y":
                        mon = card["categories"].get("monetary")
                        rs = st["rate_scores"].get(home)
                        if mon is None or mon.get("stale") or (mon.get("coverage") or 0) == 0 or rs is None:
                            continue
                        raw1, raw2 = int(mon["score_cell"]), rate_signal(rs.z, rs.rate_score)
                    elif comp == "real_yield_10y":
                        if ry is None or ry.stale:
                            continue
                        raw1, raw2 = int(ry.score), rate_signal(ry.z, ry.score)
                    else:
                        continue
                    n1 += cw * sgn * raw1
                    n2 += cw * sgn * raw2
                    ws += cw
                if ws > 0:
                    v1[name], v2[name], w[name] = n1 / ws, n2 / ws, weight
            elif name == "sentiment":
                s = st["sent"].get(sym)
                if s is not None:
                    v1[name] = v2[name] = float(fc.get("sign", 1)) * s
                    w[name] = weight
        rows.append({"symbol": sym, "board": c["type"], "v1": v1, "v2": v2, "w": w})
    return rows


# ---------------------------------------------------------------------------
# Scores + contributions (v1 = production formula, v2 = rule N-c)
# ---------------------------------------------------------------------------

def contributions(row: dict, inp: Inputs, version: str, sigma: dict | None = None) -> dict:
    """{factor: contribution}, summing to the instrument score."""
    vals = row[version]
    if version == "v2":
        vals = {f: max(-CLIP, min(CLIP, x / sigma[row["board"]][f])) for f, x in vals.items()}
    w = row["w"]
    if row["board"] == "fx_pairs":
        scale = float(inp.inst.get("scale", 5))
        div = float(inp.inst.get("pair_divisor", 2))
        w_s = w.get("sentiment", 0.0)
        cats = [f for f in vals if f != "sentiment"]
        W = sum(w[f] for f in cats)
        out = {f: (w[f] * vals[f] / W if W else 0.0) / (1 + w_s) * scale / div for f in cats}
        if "sentiment" in vals:
            out["sentiment"] = w_s * vals["sentiment"] / (1 + w_s) * scale / div
        return out
    scale = float(inp.inst.get("scale", 5)) if row["board"] == "us_dollar" else float(inp.xcfg.get("scale", 5))
    sign = row.get("sign", 1.0)
    W = sum(w[f] for f in vals)
    return {f: (sign * w[f] * vals[f] / W * scale if W else 0.0) for f in vals}


def board_rows(st: dict, inp: Inputs) -> list[dict]:
    return fx_rows(st, inp) + cross_rows(st, inp)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def validate(inp: Inputs) -> int:
    pub = json.loads((ROOT / "public" / "data" / "economic.json").read_text())
    t = pd.Timestamp(pub["as_of"])
    st = state_at(inp, t)
    worst = {"production_path": 0.0, "v1_decomposition": 0.0}
    n = 0
    prod_fx = {i["symbol"]: i["score"] for i in st["payload"]["instruments"]}
    prod_x = {s: r["score_precise"] for s, r in st["cross"].items()}
    pub_fx = {i["symbol"]: i["score"] for i in pub["instruments"]}
    pub_x = {i["symbol"]: i["score_precise"] for i in pub["crossasset"]["instruments"]}
    for sym, v in pub_fx.items():
        worst["production_path"] = max(worst["production_path"], abs(prod_fx[sym] - v)); n += 1
    for sym, v in pub_x.items():
        worst["production_path"] = max(worst["production_path"], abs(prod_x[sym] - v)); n += 1
    for row in board_rows(st, inp):
        s = sum(contributions(row, inp, "v1").values())
        ref = pub_fx.get(row["symbol"], pub_x.get(row["symbol"]))
        worst["v1_decomposition"] = max(worst["v1_decomposition"], abs(s - ref))
    print(f"validate @ {t}: {n} scores; max |diff| production path = {worst['production_path']:.3e}, "
          f"v1 decomposition = {worst['v1_decomposition']:.3e}")
    return 0 if max(worst.values()) < 1e-9 else 1


def collect(inp: Inputs, weeks: list[pd.Timestamp]) -> pd.DataFrame:
    recs = []
    for i, t in enumerate(weeks):
        st = state_at(inp, t)
        prod = {i_["symbol"]: i_["score"] for i_ in st["payload"]["instruments"]}
        prod.update({s: r["score_precise"] for s, r in st["cross"].items()})
        for row in board_rows(st, inp):
            recs.append({"week": t, "symbol": row["symbol"], "board": row["board"],
                         "sign": row.get("sign", 1.0), "v1": row["v1"], "v2": row["v2"],
                         "w": row["w"], "score_prod": prod[row["symbol"]]})
        if i % 20 == 0:
            log.warning("week %d/%d %s", i + 1, len(weeks), t.date())
    return pd.DataFrame(recs)


def sigmas(df: pd.DataFrame) -> dict:
    out = {}
    for board in BOARDS:
        sub = df[df["board"] == board]
        out[board] = {f: float(np.std([r[f] for r in sub["v2"] if f in r], ddof=1))
                      for f in FACTORS[board]}
    return out


def influence(df: pd.DataFrame, inp: Inputs, version: str, sigma=None) -> dict:
    out = {}
    for board in BOARDS:
        sub = df[df["board"] == board]
        acc = {f: 0.0 for f in FACTORS[board]}
        for _, r in sub.iterrows():
            row = {"board": board, "sign": r["sign"], "v1": r["v1"], "v2": r["v2"], "w": r["w"]}
            for f, c in contributions(row, inp, version, sigma).items():
                acc[f] += abs(c)
        tot = sum(acc.values())
        share = {f: acc[f] / tot for f in acc}
        rates_key = "monetary" if board in ("fx_pairs", "us_dollar") else "rates"
        out[board] = {"macro": sum(share[f] for f in CATS), "rates": share[rates_key],
                      "sentiment": share["sentiment"], **{f"_{f}": share[f] for f in share}}
    return out


def scores(df: pd.DataFrame, inp: Inputs, version: str, sigma=None) -> pd.Series:
    return pd.Series([sum(contributions({"board": r["board"], "sign": r["sign"], "v1": r["v1"],
                                         "v2": r["v2"], "w": r["w"]}, inp, version, sigma).values())
                      for _, r in df.iterrows()], index=df.index)


def main() -> int:
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["validate", "measure"])
    ap.add_argument("--indicators", default=str(er.INDICATORS_YAML))
    ap.add_argument("--now", default=None, help="calendar load instant (default: public as_of)")
    ap.add_argument("--cache", default=str(CACHE))
    args = ap.parse_args()
    pub_as_of = pd.Timestamp(json.loads((ROOT / "public/data/economic.json").read_text())["as_of"])
    inp = Inputs(Path(args.indicators), pd.Timestamp(args.now) if args.now else pub_as_of)
    if args.cmd == "validate":
        return validate(inp)
    last = pub_as_of.normalize() - pd.Timedelta(days=(pub_as_of.weekday() - 4) % 7) + pd.Timedelta(hours=21)
    if last > pub_as_of:
        last -= pd.Timedelta(days=7)
    weeks = fridays(last)
    df = collect(inp, weeks)
    Path(args.cache).parent.mkdir(parents=True, exist_ok=True)
    pickle.dump({"df": df, "weeks": weeks}, open(args.cache, "wb"))
    sig = sigmas(df)
    print(f"window {weeks[0].date()} .. {weeks[-1].date()} ({len(weeks)} weeks)")
    print("sigma:", json.dumps(sig, indent=1))
    print("influence v1:", json.dumps(influence(df, inp, "v1"), indent=1))
    print("influence v2:", json.dumps(influence(df, inp, "v2", sig), indent=1))
    worst = (scores(df, inp, "v1") - df["score_prod"]).abs().max()
    print(f"v1 decomposition vs production, all weeks: max |diff| = {worst:.3e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
