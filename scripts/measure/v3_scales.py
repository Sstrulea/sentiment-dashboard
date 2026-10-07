"""Scoring v3 "swing" (2026-10-07) — measure the constants once (instrumentation,
never imported by production).

Weekly reconstruction (Fridays 21:00 UTC, 2023-09-22 .. 2026-10-02) through the
production functions of scripts/measure/factor_scales.py (state_at: calendar +
policy rate + scoring_view cut at t, COT cells counting only with
spec_extreme_6m, the v2 board as it scored at t). Policy rates come from
src.policy_rate.policy_rate_history (FF calendar converted + decisions.parquet),
never data/policy_rates.yaml. Prices: FRED only.

Measured (written to the config by hand with this script's numbers, then
re-derived only on a structural change):
  FX        σ(currency, category) of score_precise (24); σ_macro of the Macro mean
            (before dividing) over all currency-weeks; σ_carry of (policy − mean
            of the 8); σ_cot of the currency COT cell over the 8 (USD = 0)
  cross     σ_y of the 63-observation change (5-observation averaged ends) per
            yield over its daily history since 2014-01-01; per instrument σ of the
            Macro and Rates blocks over the weekly window; σ_cot_metal
  labels    RMS = sqrt(mean(score²)) per instrument over the last 156 weeks;
            thresholds per board = p55 / p90 of |score / RMS| over the last 53 weeks

Acceptance (pre-registered): 1) currency-level FX IC at 1/2/4 weeks; 2) Very
share per instrument; 3) daily FRED check of the indices' Rates block, 2004-2026.

    python scripts/measure/v3_scales.py collect     # weekly reconstruction → cache
    python scripts/measure/v3_scales.py measure [--config]   # constants, scores, criteria, csv
    (--config: use the constants already in the YAML configs instead of re-measuring)
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
sys.path.insert(0, str(ROOT / "scripts" / "measure"))

import factor_scales as fs  # noqa: E402
from src import economic_render as er  # noqa: E402
from src.crossasset_compute import (V3_METAL_WEIGHTS, V3_RATE_END_N, V3_RATE_W, V3_SCALE,  # noqa: E402
                                    v3_index_combine, v3_macro_raw, v3_yield_change)
from src.economic_compute import (V3_DISPLAY_SCALE, V3_FX_WEIGHTS, V3_MACRO_CATS,  # noqa: E402
                                  bias_label, v3_combine, v3_currency_blocks, v3_macro_mean)
from src.policy_rate import load_decisions, policy_rate_history, rate_at  # noqa: E402
from src.rate_compute import _end_changes, _series_for, max_age_for  # noqa: E402
from src.momentum_common import bday_lag  # noqa: E402

log = logging.getLogger("v3_scales")

CCYS = ("USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF")
CACHE = ROOT / "scripts" / "measure" / "__pycache__" / "v3_weeks.pkl"
FRED_CACHE = ROOT / "scripts" / "measure" / "__pycache__"
SIGMA_Y_SINCE = pd.Timestamp("2014-01-01")
YIELDS = {"USD": "USD", "EUR": "EUR", "JPY": "JPY", "GBP": "GBP", "REAL10": "DFII10"}
REAL_MAX_AGE_BD = 7
RMS_WEEKS, THR_WEEKS = 156, 53
REFERENCE = {"sigma_macro": 0.585, "sigma_carry": 1.62, "sigma_cot": 1.77,
             "fx_thresholds": (0.70, 1.51), "labour_USD": 0.30, "labour_CHF": 1.00}


# ---------------------------------------------------------------------------
# Collection (production state per Friday) — cached
# ---------------------------------------------------------------------------

def collect(cache: Path) -> None:
    pub_as_of = pd.Timestamp(json.loads((ROOT / "public/data/economic.json").read_text())["as_of"])
    inp = fs.Inputs(er.INDICATORS_YAML, pub_as_of)
    last = pd.Timestamp("2026-10-02 21:00")
    weeks = fs.fridays(last)
    recs = []
    for i, t in enumerate(weeks):
        st = fs.state_at(inp, t)
        cards = st["payload"]["currencies"]
        recs.append({
            "week": t,
            "cats": {c: {k: {"score_precise": v["score_precise"], "coverage": v["coverage"]}
                         for k, v in cards[c]["categories"].items() if k in V3_MACRO_CATS}
                     for c in cards},
            "fx_cells": dict(st["fx_cells"]),
            "metal_cells": {s: v for s, v in st["sent"].items() if s in ("GOLD", "SILVER")},
            "v2_fx": {x["symbol"]: (x.get("score_v2", x["score"]), x.get("bias_v2", x["bias"]))
                      for x in st["payload"]["instruments"]},
            "v2_cross": {s: (r["score_precise"], r["bias_label"]) for s, r in st["cross"].items()},
        })
        if i % 20 == 0:
            log.warning("week %d/%d %s", i + 1, len(weeks), t.date())
    cache.parent.mkdir(parents=True, exist_ok=True)
    pickle.dump({"weeks": weeks, "recs": recs}, open(cache, "wb"))


# ---------------------------------------------------------------------------
# Yields
# ---------------------------------------------------------------------------

def yield_series() -> dict[str, tuple[list, list, str | None]]:
    """{name: (dates, ys, source of the latest obs)} — 2Y from data/rates.parquet,
    the US real 10Y (DFII10) from data/real_yields.parquet."""
    rates = pd.read_parquet(er.RATES_PARQUET)
    out = {}
    for name, ccy in YIELDS.items():
        if name == "REAL10":
            ry = pd.read_parquet(er.REAL_YIELDS_PARQUET)
            ry = ry[ry["series"] == "DFII10"].sort_values("date")
            ry = ry[ry["yield_pct"].notna()]
            out[name] = ([pd.Timestamp(d).date() for d in ry["date"]], [float(v) for v in ry["yield_pct"]], None)
            continue
        d, y = _series_for(rates, ccy)
        sub = rates[rates["currency"] == ccy].sort_values("date")
        out[name] = (d, y, str(sub["source"].iloc[-1]) if len(sub) else None)
    return out


def sigma_y(series) -> dict[str, float]:
    out = {}
    for name, (d, y, _src) in series.items():
        ch = _end_changes(y, V3_RATE_W, V3_RATE_END_N)
        ch_dates = d[V3_RATE_W + V3_RATE_END_N - 1:]
        vals = [c for c, dt in zip(ch, ch_dates) if pd.Timestamp(dt) >= SIGMA_Y_SINCE]
        out[name] = float(np.std(vals, ddof=1))
    return out


def yield_signal_prod(t: pd.Timestamp, sig_y: dict, _cache={}) -> dict:
    """The production v3_yield_signals at t (for the cross-check)."""
    from src.crossasset_compute import v3_yield_signals
    if "rates" not in _cache:
        _cache["rates"] = pd.read_parquet(er.RATES_PARQUET)
        _cache["real"] = pd.read_parquet(er.REAL_YIELDS_PARQUET)
    return v3_yield_signals(_cache["rates"], _cache["real"], t.date(), sig_y)


def yield_signal(series, name: str, t: pd.Timestamp, sig_y: dict) -> float | None:
    """−Δ63 / σ_y at t; None when missing or stale (latest obs older than the
    source's max age, as the rate pillar judges it)."""
    d, y, src = series[name]
    delta, latest = v3_yield_change(d, y, t.date())
    if delta is None or latest is None:
        return None
    age = REAL_MAX_AGE_BD if name == "REAL10" else max_age_for(src)
    if bday_lag(latest, t.date()) > age:
        return None
    return -delta / sig_y[name]


# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------

def _std(v):
    v = [x for x in v if x is not None and not (isinstance(x, float) and math.isnan(x))]
    return float(np.std(v, ddof=1)) if len(v) > 1 else float("nan")


def measure_constants(recs, weeks, hist, ycfg, xcfg, series) -> dict:
    k: dict = {}
    # σ(currency, category)
    sig_cc = {c: {cat: _std([r["cats"][c][cat]["score_precise"] for r in recs
                             if r["cats"][c].get(cat, {}).get("coverage", 0) > 0])
                  for cat in V3_MACRO_CATS} for c in CCYS}
    k["sigma_ccy"] = sig_cc
    mm = [v3_macro_mean(r["cats"][c], sig_cc[c]) for r in recs for c in CCYS]
    k["sigma_macro"] = _std(mm)
    carry = []
    for r in recs:
        rt = rate_at(hist, r["week"])
        mean8 = np.mean([rt[c] for c in CCYS if c in rt])
        carry += [rt[c] - mean8 for c in CCYS if c in rt]
    k["sigma_carry"] = _std(carry)
    k["sigma_cot"] = _std([0.0 if c == "USD" else r["fx_cells"].get(c) for r in recs for c in CCYS])
    k["sigma_y"] = sigma_y(series)
    # cross-asset blocks
    mac, rat = {}, {}
    for r in recs:
        for sym, cfg in xcfg["instruments"].items():
            home = cfg["home_ccy"]
            mac.setdefault(sym, []).append(v3_macro_raw(cfg, r["cats"].get(home, {}), sig_cc[home]))
            rat.setdefault(sym, []).append(rates_raw(sym, cfg, r["week"], series, k["sigma_y"]))
    k["sigma_macro_inst"] = {s: _std(v) for s, v in mac.items()}
    k["sigma_rates_inst"] = {s: _std(v) for s, v in rat.items()}
    k["sigma_cot_metal"] = _std([r["metal_cells"].get(s) for r in recs for s in ("GOLD", "SILVER")])
    return k


def rates_raw(sym, cfg, t, series, sig_y) -> float | None:
    """Indices: mean of the home-currency 2Y and US real 10Y signals; metals: US 2Y."""
    names = ["USD"] if cfg["type"] == "metal" else [cfg["home_ccy"], "REAL10"]
    vals = [yield_signal(series, n, t, sig_y) for n in names if n in series]
    vals = [v for v in vals if v is not None]
    return float(np.mean(vals)) if vals else None


# ---------------------------------------------------------------------------
# v3 scores
# ---------------------------------------------------------------------------

def currency_scores(r, hist, k, dxy=False) -> dict:
    rt = rate_at(hist, r["week"])
    mean8 = np.mean([rt[c] for c in CCYS if c in rt]) if rt else None
    out = {}
    for c in CCYS:
        mmean = v3_macro_mean(r["cats"][c], k["sigma_ccy"][c])
        carry_raw = (rt[c] - mean8) if c in rt else None
        cot = 0.0 if c == "USD" else r["fx_cells"].get(c)
        blocks = v3_currency_blocks(mmean, carry_raw, cot, k)
        s, _ = v3_combine(blocks, V3_FX_WEIGHTS)
        out[c] = {"S": s, "blocks": blocks}
    usd_dxy = v3_currency_blocks(v3_macro_mean(r["cats"]["USD"], k["sigma_ccy"]["USD"]),
                                 (rt["USD"] - mean8) if "USD" in rt else None,
                                 r["fx_cells"].get("DXY"), k)
    out["_USD_ROW"] = {"S": v3_combine(usd_dxy, V3_FX_WEIGHTS)[0], "blocks": usd_dxy}
    return out


def v3_week(r, hist, k, inst_cfg, xcfg, series) -> dict:
    cs = currency_scores(r, hist, k)
    scores = {}
    for sym, c in inst_cfg["instruments"].items():
        if c["type"] == "single":
            s = cs["_USD_ROW"]["S"]
            scores[sym] = None if s is None else s * V3_DISPLAY_SCALE
        else:
            sb, sq = cs[c["base"]]["S"], cs[c["quote"]]["S"]
            scores[sym] = None if sb is None or sq is None else (sb - sq) * V3_DISPLAY_SCALE
    for sym, cfg in xcfg["instruments"].items():
        home = cfg["home_ccy"]
        m_raw = v3_macro_raw(cfg, r["cats"].get(home, {}), k["sigma_ccy"][home])
        r_raw = rates_raw(sym, cfg, r["week"], series, k["sigma_y"])
        m = None if m_raw is None else m_raw / k["sigma_macro_inst"][sym]
        rb = None if r_raw is None else r_raw / k["sigma_rates_inst"][sym]
        if cfg["type"] == "index":
            s, _ = v3_index_combine(m, rb)
        else:
            cell = r["metal_cells"].get(sym)
            cot = None if cell is None else cell / k["sigma_cot_metal"]
            s, _ = v3_combine({"macro": m, "rates": rb, "cot": cot}, V3_METAL_WEIGHTS)
        scores[sym] = None if s is None else s * V3_SCALE
    # /strength: mean over the 7 pairs of 2.5 × (Macro_base − Macro_quote)
    macro = {c: cs[c]["blocks"]["macro"] for c in CCYS}
    strength = {}
    for c in CCYS:
        vals = []
        for sym, ic in inst_cfg["instruments"].items():
            if ic["type"] != "fx" or c not in (ic["base"], ic["quote"]):
                continue
            mb, mq = macro[ic["base"]], macro[ic["quote"]]
            if mb is None or mq is None:
                continue
            v = V3_DISPLAY_SCALE * (mb - mq)
            vals.append(v if ic["base"] == c else -v)
        strength[c] = float(np.mean(vals)) if vals else None
    return {"scores": scores, "ccy": {c: cs[c]["S"] for c in CCYS}, "strength": strength}


# ---------------------------------------------------------------------------
# Criteria
# ---------------------------------------------------------------------------

def fred_usd_values() -> dict[str, pd.Series]:
    """USD value of one unit of each currency (FRED H.10), USD = 1."""
    out = {}
    for c, (sid, inv) in fs.FRED_FX.items():
        s = fs._fred(sid, FRED_CACHE)
        out[c] = (1.0 / s) if inv else s
    return out


def _first_on_or_after(s: pd.Series, d: pd.Timestamp):
    x = s[s.index >= d]
    return (x.index[0], float(x.iloc[0])) if len(x) else (None, None)


def _spearman(a, b) -> float:
    a, b = pd.Series(a).rank(), pd.Series(b).rank()
    return float(a.corr(b))


def currency_ic(weekly_ccy: dict, prices: dict, last_price_day: pd.Timestamp) -> dict:
    """Mean over weeks of the cross-sectional Spearman (8 currencies) between
    S_c and the demeaned log return vs USD over h weeks."""
    out = {}
    for h in (1, 2, 4):
        ics = []
        for t, s in weekly_ccy.items():
            monday = (t.normalize() + pd.Timedelta(days=(7 - t.weekday()) % 7 or 7))
            rets, sc = [], []
            ok = True
            for c in CCYS:
                if c == "USD":
                    lr = 0.0
                else:
                    series = prices[c][prices[c].index <= last_price_day]
                    d0, p0 = _first_on_or_after(series, monday)
                    if d0 is None:
                        ok = False
                        break
                    d1, p1 = _first_on_or_after(series, d0 + pd.Timedelta(days=7 * h))
                    if d1 is None:
                        ok = False
                        break
                    lr = math.log(p1 / p0)
                if s.get(c) is None:
                    ok = False
                    break
                rets.append(lr)
                sc.append(s[c])
            if not ok:
                continue
            rets = np.array(rets) - np.mean(rets)
            ics.append(_spearman(sc, rets))
        out[h] = {"ic": float(np.mean(ics)), "weeks": len(ics)}
    return out


def index_rates_check(series, sig_y, sig_rates) -> dict:
    """Weekly (Friday) 2004-2026: Spearman of the index Rates signal vs the next
    2- and 4-week FRED return. Entry/exit as in currency_ic."""
    fred = {"SP500": ("SP500", "USD"), "NASDAQ": ("NASDAQCOM", "USD"), "DJIA": ("DJIA", "USD"),
            "NIKKEI": ("NIKKEI225", "JPY")}
    out = {}
    fridays = pd.date_range("2004-01-02", "2026-10-02", freq="W-FRI")
    for sym, (sid, home) in fred.items():
        px = fs._fred(sid, FRED_CACHE)
        res = {"first_price": str(px.index.min().date())}
        for h in (2, 4):
            sig, ret = [], []
            for f in fridays:
                if f < px.index.min():
                    continue
                vals = [yield_signal(series, n, pd.Timestamp(f) + pd.Timedelta(hours=21), sig_y)
                        for n in (home, "REAL10")]
                vals = [v for v in vals if v is not None]
                if not vals:
                    continue
                monday = f + pd.Timedelta(days=3)
                d0, p0 = _first_on_or_after(px, monday)
                if d0 is None:
                    continue
                d1, p1 = _first_on_or_after(px, d0 + pd.Timedelta(days=7 * h))
                if d1 is None:
                    continue
                sig.append(np.mean(vals) / sig_rates.get(sym, 1.0))
                ret.append(math.log(p1 / p0))
            res[f"ic_{h}w"] = _spearman(sig, ret)
            res[f"n_{h}w"] = len(sig)
        out[sym] = res
    return out


# ---------------------------------------------------------------------------

def rms(v):
    v = [x for x in v if x is not None]
    return float(math.sqrt(np.mean(np.square(v)))) if v else None


def main() -> int:
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["collect", "measure"])
    ap.add_argument("--cache", default=str(CACHE))
    ap.add_argument("--config", action="store_true", help="use every constant in the configs (σ, RMS, thresholds)")
    ap.add_argument("--sigma-config", action="store_true",
                    help="σ from the configs, RMS and thresholds measured (the second pass)")
    ap.add_argument("--csv", default=str(ROOT / "docs" / "scoring-v3-before-after.csv"))
    ap.add_argument("--out", default=None, help="write the full result as JSON here")
    a = ap.parse_args()
    if a.cmd == "collect":
        collect(Path(a.cache))
        return 0

    d = pickle.load(open(a.cache, "rb"))
    weeks, recs = d["weeks"], d["recs"]
    inst_cfg = yaml.safe_load(er.INSTRUMENTS_YAML.read_text())
    xcfg = yaml.safe_load(er.CROSSASSET_YAML.read_text())
    hist = policy_rate_history(pd.read_parquet(ROOT / "data/economic_calendar_ff.parquet"), load_decisions())
    series = yield_series()
    if a.config or a.sigma_config:
        k = config_constants(inst_cfg, xcfg, sigma_only=a.sigma_config)
    else:
        k = measure_constants(recs, weeks, hist, None, xcfg, series)

    res = {"window": [str(weeks[0]), str(weeks[-1]), len(weeks)], "constants": k}
    per_week = {r["week"]: v3_week(r, hist, k, inst_cfg, xcfg, series) for r in recs}

    # RMS per instrument (last 156 weeks) and thresholds per board (last 53 weeks)
    last156, last53 = weeks[-RMS_WEEKS:], weeks[-THR_WEEKS:]
    fx_syms = list(inst_cfg["instruments"]); x_syms = list(xcfg["instruments"])
    rms_inst = {s: rms([per_week[w]["scores"][s] for w in last156]) for s in fx_syms + x_syms}
    rms_str = {c: rms([per_week[w]["strength"][c] for w in last156]) for c in CCYS}
    if a.config:
        rms_inst.update(k.get("rms", {}))
        rms_str.update(k.get("rms_strength", {}))
    thr = {}
    for board, syms, getter, rm in (("fx", fx_syms, "scores", rms_inst), ("cross", x_syms, "scores", rms_inst),
                                    ("strength", CCYS, "strength", rms_str)):
        z = [abs(per_week[w][getter][s] / rm[s]) for w in last53 for s in syms
             if per_week[w][getter][s] is not None and rm.get(s)]
        thr[board] = {"mild": float(np.quantile(z, .55)), "very": float(np.quantile(z, .90))}
    if a.config:
        thr = k.get("thresholds", thr)
    res["rms"], res["rms_strength"], res["thresholds"] = rms_inst, rms_str, thr

    # labels, Very share (criterion 2), csv
    rows = []
    very = {}
    for w in weeks:
        pw = per_week[w]
        r = next(x for x in recs if x["week"] == w)
        for s in fx_syms + x_syms:
            board = "fx" if s in fx_syms else "cross"
            sc = pw["scores"][s]
            t = thr[board]
            lab = bias_label(sc / rms_inst[s], {"mild": round(t["mild"], 2), "very": round(t["very"], 2)}) \
                if sc is not None and rms_inst.get(s) else None
            v2 = r["v2_fx"].get(s) or r["v2_cross"].get(s)
            rows.append({"week": w, "symbol": s, "board": board, "score_v2": v2[0], "bias_v2": v2[1],
                         "score_v3": sc, "z_v3": None if sc is None else sc / rms_inst[s], "bias_v3": lab})
            very.setdefault(s, []).append(lab is not None and lab.startswith("Very"))
    pd.DataFrame(rows).to_csv(a.csv, index=False, float_format="%.6f")
    res["very_share"] = {s: round(100 * float(np.mean(v)), 1) for s, v in very.items()}

    # criterion 1
    prices = fred_usd_values()
    res["ic_fx"] = currency_ic({w: per_week[w]["ccy"] for w in weeks}, prices, pd.Timestamp("2026-10-02"))
    # criterion 3
    res["index_rates_check"] = index_rates_check(series, k["sigma_y"], k["sigma_rates_inst"])
    res["reference"] = REFERENCE
    txt = json.dumps(res, indent=1, default=str)
    if a.out:
        Path(a.out).write_text(txt)
    print(txt)
    return 0


def config_constants(inst_cfg, xcfg, sigma_only: bool = False) -> dict:
    v3 = inst_cfg.get("v3") or {}
    xv3 = xcfg.get("v3") or {}
    if sigma_only:
        return {"sigma_ccy": v3["sigma_ccy"], "sigma_macro": v3["sigma_macro"], "sigma_carry": v3["sigma_carry"],
                "sigma_cot": v3["sigma_cot"], "sigma_y": xv3["sigma_y"], "sigma_macro_inst": xv3["sigma_macro"],
                "sigma_rates_inst": xv3["sigma_rates"], "sigma_cot_metal": xv3["sigma_cot_metal"]}
    return {"sigma_ccy": v3["sigma_ccy"], "sigma_macro": v3["sigma_macro"], "sigma_carry": v3["sigma_carry"],
            "sigma_cot": v3["sigma_cot"], "sigma_y": xv3["sigma_y"], "sigma_macro_inst": xv3["sigma_macro"],
            "sigma_rates_inst": xv3["sigma_rates"], "sigma_cot_metal": xv3["sigma_cot_metal"],
            "rms": {**v3.get("rms", {}), **xv3.get("rms", {})}, "rms_strength": v3.get("rms_strength", {}),
            "thresholds": {"fx": v3["thresholds"], "cross": xv3["thresholds"], "strength": v3["thresholds_strength"]}}


if __name__ == "__main__":
    sys.exit(main())
