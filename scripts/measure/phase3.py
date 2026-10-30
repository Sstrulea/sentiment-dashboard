"""Phase 3 measurement (attempt #3) against scripts/measure/prereg_phase3.yml.

    python scripts/measure/phase3.py [--out-json data/tree_runs/phase3_metrics.json]
                                     [--out-md docs/eval/phase3.md]

Refuses to run while the pre-registration has uncommitted changes. Stages:
A regime v1 coverage + known episodes + trend/range; B OECD coverage + cycle cells;
C macro_momentum cross-sectional harness and gates; D v3 decomposition (descriptive);
E attempt #3 (good news / bad price episodes); G4 ECB fixing diagnostic.
Stage H lives in scripts/measure/phase3_h.py."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "measure"))

import reaction_v0 as R  # noqa: E402
from src.tree import schema  # noqa: E402
from src.tree.backbone import CLOSES, AlignedSeries, Backbone, basket  # noqa: E402
from src.tree.macro import MacroInputs  # noqa: E402
from src.tree.reaction import ReactionInputs, ols, panel  # noqa: E402
from src.tree.regimes import RegimeV1, trend_range  # noqa: E402

PREREG = ROOT / "scripts" / "measure" / "prereg_phase3.yml"
V3_SCORES = Path.home() / ".cache" / "macro-tournament" / "scores_main.parquet"
CCYS = ["USD", "EUR", "GBP", "JPY", "CAD", "AUD", "NZD", "CHF"]
SIX = ["USD", "EUR", "GBP", "JPY", "CAD", "AUD"]
START = pd.Timestamp("2000-01-07")
HORIZONS = [2, 4, 8, 13]
SEED = 20261030
EPISODES = {"2001-09": ("2001-09-01", "2001-09-30"), "2008-09..2009-03": ("2008-09-01", "2009-03-31"),
            "2011-08": ("2011-08-01", "2011-08-31"), "2015-08": ("2015-08-01", "2015-08-31"),
            "2018-12": ("2018-12-01", "2018-12-31"), "2020-03": ("2020-03-01", "2020-03-31"),
            "2022": ("2022-01-01", "2022-12-31"), "2025-04": ("2025-04-01", "2025-04-30")}


def weekly_ic(S: pd.DataFrame, Rt: pd.DataFrame, min_cs: int = 5) -> pd.Series:
    cols = [c for c in S.columns if c in Rt.columns]
    S, Rt = S[cols], Rt[cols]
    ok = (S.notna() & Rt.notna()).sum(axis=1) >= min_cs
    return pd.Series([R.spearman(S.loc[t], Rt.loc[t]) for t in S.index[ok]], index=S.index[ok]).dropna()


def grid_stats(ics: pd.Series, h: int, start=START) -> dict:
    g = ics[R.grid_mask(ics.index, start, h)] if len(ics) else ics
    n = len(g)
    m = float(g.mean()) if n else float("nan")
    sd = float(g.std(ddof=1)) if n > 2 else float("nan")
    return {"IC": m, "t": m / (sd / np.sqrt(n)) if n > 2 and sd > 0 else float("nan"), "N_ind": n,
            "N_weeks": int(len(ics)), "sd": sd}


def welch(a: dict, b: dict) -> float:
    if not all(np.isfinite(x) for x in (a["IC"], b["IC"], a["sd"], b["sd"])) or a["N_ind"] < 3 or b["N_ind"] < 3:
        return float("nan")
    return float((a["IC"] - b["IC"]) / np.sqrt(a["sd"] ** 2 / a["N_ind"] + b["sd"] ** 2 / b["N_ind"]))


def abstention(S: pd.DataFrame, Rt: pd.DataFrame, h: int, thr: float = 0.25) -> dict:
    g = R.grid_mask(S.index, START, h)
    S, Rt = S[g], Rt.reindex(S.index)[g]
    m = S.notna() & Rt.notna()
    tot = int(m.values.sum())
    pred = m & (S.abs() >= thr)
    hit = (np.sign(S) == np.sign(Rt)) & pred
    n = int(pred.values.sum())
    return {"coverage": n / tot if tot else float("nan"), "accuracy": int(hit.values.sum()) / n if n else float("nan"),
            "n_pred": n, "n_total": tot}


# ---------------------------------------------------------------------------
# Data assembly
# ---------------------------------------------------------------------------

def assemble():
    bb = Backbone.load()
    lv = bb.basket_levels()
    last = lv.index[-1]
    fridays = pd.date_range(START, last, freq="7D")
    fwd = {h: pd.DataFrame({c: R.forward(lv[c], fridays, h, 1e4) for c in CCYS}) for h in [1] + HORIZONS}
    carry = pd.DataFrame({c: [R.level_at(bb.by[f"POL_{c}"], t - pd.Timedelta(days=1)) for t in fridays]
                          for c in CCYS}, index=fridays)
    mom = pd.DataFrame({c: [(R.level_at(lv[c], t - pd.Timedelta(days=1)) - R.level_at(lv[c], t - pd.Timedelta(days=92))) * 1e4
                            for t in fridays] for c in CCYS}, index=fridays)
    rnd = pd.DataFrame(np.random.default_rng(SEED).uniform(size=(len(fridays), 8)), index=fridays, columns=CCYS)
    return bb, lv, fridays, fwd, {"carry": carry, "momentum_13w": mom, "random": rnd}


def macro_panel(mi: MacroInputs, fridays) -> tuple[dict, pd.DataFrame]:
    keys = ["composite_eq", "composite_il", "composite_cli"]
    cats = ["Inflation", "Labor", "Growth"]
    out = {k: {} for k in keys + cats}
    cyc = []
    for c in CCYS:
        cols = {k: [] for k in out}
        for t in fridays:
            m = mi.momentum(c, t)
            for k in keys:
                cols[k].append(m[k])
            for k in cats:
                cols[k].append(m["categories"][k])
            cy = mi.cycle(c, t)
            cyc.append({"asof": t, "ccy": c, **cy})
        for k in out:
            out[k][c] = cols[k]
    frames = {k: pd.DataFrame(v, index=fridays, dtype=float) for k, v in out.items()}
    return frames, pd.DataFrame(cyc)


def v3_scores(fridays) -> dict:
    if not V3_SCORES.exists():
        return {}
    v = pd.read_parquet(V3_SCORES)
    v = v[v["view"] == "ccy"]
    v["asof"] = pd.to_datetime(v["week"]).dt.normalize()
    out = {n: v[v["variant"] == n].pivot(index="asof", columns="unit", values="score").reindex(columns=CCYS)
           for n in ("A1", "B2", "D1")}
    a, b, d = out["A1"], out["B2"], out["D1"]
    out["COT_derived"] = (a - 0.51 * b - 0.34 * d) / 0.15
    out["macro_carry"] = (0.51 * b + 0.34 * d) / 0.85
    return out


# ---------------------------------------------------------------------------
# Stages
# ---------------------------------------------------------------------------

def stage_a(fridays, lv) -> tuple[dict, pd.Series, pd.Series]:
    rv = RegimeV1()
    rows = [{"asof": t, **{k: rv.at(t)[k] for k in ("regime", "degraded")}} for t in fridays]
    reg = pd.DataFrame(rows).set_index("asof")
    reg["year"] = reg.index.year
    cov = pd.crosstab(reg["year"], reg["regime"]).to_dict(orient="index")
    deg = reg.groupby("year")["degraded"].sum().astype(int).to_dict()
    eps = {}
    for name, (a, b) in EPISODES.items():
        x = reg.loc[a:b, "regime"]
        eps[name] = x.value_counts().to_dict()
    tr = trend_range(lv, fridays)
    tr["year"] = tr.index.year
    trd = pd.crosstab(tr["year"], tr["regime"].fillna("n/a")).to_dict(orient="index")
    vol = tr.groupby("year")["vol_pct"].mean().round(1).to_dict()
    return ({"regime_by_year": cov, "degraded_by_year": deg, "episodes": eps, "trend_range_by_year": trd,
             "vol_pct_mean_by_year": vol}, reg["regime"], tr["regime"])


def stage_b(mi: MacroInputs, cyc: pd.DataFrame) -> dict:
    f = mi.frame
    cov = []
    for (c, s), g in f.groupby(["ccy", "series"]):
        p = g["period"].sort_values()
        gaps = p.diff().dt.days
        step = 31 if g["freq"].iloc[0] == "M" else 92
        cov.append({"ccy": c, "series": s, "freq": g["freq"].iloc[0], "first": str(p.min().date()),
                    "last": str(p.max().date()), "n": int(len(p)), "gaps": int((gaps > step + 5).sum()),
                    "source": ",".join(sorted(g["source"].unique()))})
    cyc = cyc.copy()
    cyc["year"] = pd.to_datetime(cyc["asof"]).dt.year
    c16 = {c: g.groupby("year")["cell16"].value_counts().unstack(fill_value=0).to_dict(orient="index")
           for c, g in cyc.groupby("ccy")}
    tot16 = cyc.groupby(["ccy", "cell16"]).size().unstack(fill_value=0).to_dict(orient="index")
    tot8 = cyc.groupby(["ccy", "cell8"]).size().unstack(fill_value=0).to_dict(orient="index")
    missing = cyc.assign(m=cyc["cell16"].isna()).groupby("ccy")["m"].sum().astype(int).to_dict()
    return {"coverage": cov, "cell16_by_ccy_year": c16, "cell16_totals": tot16, "cell8_totals": tot8,
            "weeks_without_cell": missing}


def stage_c(frames, fwd, base, regime, cyc, v3) -> dict:
    res = {"total": {}, "subperiods": {}, "by_regime": {}, "cycle_cells": {}, "abstention": {},
           "v3_window": {}, "categories_by_regime": {}}
    prereg = {"harness": {"halves": {"first": ["2000-01-01", "2012-12-31"], "second": ["2013-01-01", "end"]}}}
    scores = {"composite_eq": frames["composite_eq"], "composite_il": frames["composite_il"],
              "composite_cli": frames["composite_cli"], "Inflation": frames["Inflation"],
              "Labor": frames["Labor"], "Growth": frames["Growth"], **base}
    subs = [(pd.Timestamp("2000-01-01"), pd.Timestamp("2008-12-31")), (pd.Timestamp("2009-01-01"), pd.Timestamp("2019-12-31")),
            (pd.Timestamp("2020-01-01"), pd.Timestamp("2026-12-31"))]
    stress = regime != "calm"
    for h in HORIZONS:
        Rt = fwd[h]
        res["total"][h], res["subperiods"][h], res["by_regime"][h] = {}, {}, {}
        for name, S in scores.items():
            m = R.cs_metrics(S, Rt, h, START, prereg)
            ics = weekly_ic(S, Rt)
            g = grid_stats(ics, h)
            m.update({"IC": g["IC"], "t": g["t"], "N_ind": g["N_ind"]})
            res["total"][h][name] = m
            res["subperiods"][h][name] = [grid_stats(ics[(ics.index >= a) & (ics.index <= b)], h) for a, b in subs]
            reg = regime.reindex(ics.index)
            res["by_regime"][h][name] = {r: grid_stats(ics[reg == r], h) for r in sorted(regime.dropna().unique())}
            res["by_regime"][h][name]["stress"] = grid_stats(ics[stress.reindex(ics.index).fillna(False)], h)
        res["abstention"][h] = abstention(frames["composite_eq"], Rt, h)
        # cycle cells: pooled, cross-sectionally demeaned score
        S = frames["composite_eq"]
        Sd = S.sub(S.mean(axis=1), axis=0)
        long = Sd.stack().rename("s").to_frame().join(Rt.stack().rename("r"), how="inner").reset_index()
        long.columns = ["asof", "ccy", "s", "r"]
        long = long.merge(cyc[["asof", "ccy", "cell8"]], on=["asof", "ccy"], how="left")
        long = long[R.grid_mask(pd.DatetimeIndex(long["asof"]), START, h)]
        cells = {}
        for cell, g in long.dropna(subset=["cell8"]).groupby("cell8"):
            ic = R.spearman(g["s"], g["r"])
            cells[cell] = {"IC": ic, "N": int(len(g)), "t": R.t_of_ic(ic, len(g))}
        res["cycle_cells"][h] = cells
        if v3:
            w0 = v3["A1"].dropna(how="all").index.min()
            res["v3_window"][h] = {}
            for name, S in {"composite_eq": frames["composite_eq"], "v3_A1": v3["A1"], "v3_macro_B2": v3["B2"],
                            "carry": base["carry"], "momentum_13w": base["momentum_13w"]}.items():
                ics = weekly_ic(S.reindex(Rt.index)[Rt.index >= w0], Rt[Rt.index >= w0])
                res["v3_window"][h][name] = grid_stats(ics, h, w0)
    # categories with sign by regime (h = 4 and 13)
    for h in (4, 13):
        res["categories_by_regime"][h] = {k: res["by_regime"][h][k] for k in ("Inflation", "Labor", "Growth")}
    return res


def gates_c(c: dict) -> dict:
    out = {}
    for h in HORIZONS:
        m, ca = c["total"][h]["composite_eq"], c["total"][h]["carry"]
        sp = c["subperiods"][h]["composite_eq"]
        signs = [np.sign(s["IC"]) for s in sp if np.isfinite(s["IC"])]
        crit = {"IC>=0.05": bool(np.isfinite(m["IC"]) and m["IC"] >= 0.05),
                "t>=2.5": bool(np.isfinite(m["t"]) and m["t"] >= 2.5),
                "same_sign_3_subperiods": len(signs) == 3 and len(set(signs)) == 1 and np.sign(m["IC"]) == signs[0],
                "IC-carry>=0.02": bool(np.isfinite(m["IC"]) and np.isfinite(ca["IC"]) and m["IC"] - ca["IC"] >= 0.02)}
        out[f"C composite_eq @{h}w"] = {"criteria": crit, "pass": all(crit.values())}
    br = c["by_regime"][4]
    calm, st = br["composite_eq"]["calm"], br["composite_eq"]["stress"]
    t = welch(calm, st)
    out["C S1 calm>stress @4w"] = {"criteria": {"diff>0": bool(np.isfinite(t) and calm["IC"] > st["IC"]),
                                                "t>=2": bool(np.isfinite(t) and t >= 2)},
                                   "pass": bool(np.isfinite(t) and calm["IC"] > st["IC"] and t >= 2), "t": t}
    ic_calm, ic_inf = br["Inflation"]["calm"], br["Inflation"].get("inflation_shock", {"IC": float("nan")})
    crit = {"calm IC>0 t>=2": bool(np.isfinite(ic_calm["t"]) and ic_calm["IC"] > 0 and ic_calm["t"] >= 2),
            "inflation_shock IC<=0": bool(np.isfinite(ic_inf["IC"]) and ic_inf["IC"] <= 0)}
    out["C S2 inflation impulse calm>0, shock<=0 @4w"] = {"criteria": crit, "pass": all(crit.values())}
    return out


def stage_d(v3, frames, fwd, regime, trreg) -> dict:
    if not v3:
        return {"missing": f"{V3_SCORES} not found"}
    comps = {"A1": v3["A1"], "macro_B2": v3["B2"], "carry_D1": v3["D1"], "COT_derived": v3["COT_derived"],
             "macro_plus_carry": v3["macro_carry"]}
    w0 = v3["A1"].dropna(how="all").index.min()
    out = {"window": [str(w0.date()), str(v3["A1"].dropna(how="all").index.max().date())], "ic": {},
           "usd_cot_check": float(v3["COT_derived"]["USD"].abs().max())}
    for h in (2, 4):
        Rt = fwd[h][fwd[h].index >= w0]
        out["ic"][h] = {}
        for name, S in comps.items():
            S = S.reindex(Rt.index)
            out["ic"][h][name] = {"8": grid_stats(weekly_ic(S, Rt), h, w0), "6": grid_stats(weekly_ic(S[SIX], Rt[SIX]), h, w0)}
    names = list(comps)
    corr = {}
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            A, B = comps[a], comps[b].reindex(comps[a].index)
            v = [R.spearman(A.loc[t], B.loc[t]) for t in A.index]
            corr[f"{a}~{b}"] = float(np.nanmean(v))
    mm = frames["composite_eq"].reindex(v3["B2"].index)
    corr["macro_B2~macro_momentum_eq"] = float(np.nanmean([R.spearman(v3["B2"].loc[t], mm.loc[t]) for t in v3["B2"].index]))
    out["rank_corr"] = corr
    split = pd.Timestamp("2025-02-28")
    out["A1_split"] = {}
    for h in (2, 4):
        Rt = fwd[h][fwd[h].index >= w0]
        ics = weekly_ic(v3["A1"].reindex(Rt.index), Rt)
        out["A1_split"][h] = {"first": grid_stats(ics[ics.index <= split], h, w0),
                              "second": grid_stats(ics[ics.index > split], h, w0),
                              "by_regime_v1": {r: grid_stats(ics[regime.reindex(ics.index) == r], h, w0)
                                               for r in regime.reindex(ics.index).dropna().unique()},
                              "by_trend_range": {r: grid_stats(ics[trreg.reindex(ics.index) == r], h, w0)
                                                 for r in ("trend", "range")}}
    return out


def pick_units(mask: pd.DataFrame, gap_days: int = 28) -> list[tuple]:
    """(Friday, currency) where mask is True, skipping a currency's Fridays within
    gap_days after its previous pick (episode start + 4-week exclusion)."""
    out = []
    for c in mask.columns:
        last = None
        for t in mask.index[mask[c].fillna(False).to_numpy(bool)]:
            if last is None or (t - last).days >= gap_days:
                out.append((t, c))
                last = t
    return out


def control_mask(gnbp: pd.DataFrame, past4: pd.DataFrame, q: pd.Series) -> pd.DataFrame:
    """gnbp_4w == 0 and the past 4-week basket return below the Friday's quintile threshold."""
    return (gnbp.reindex_like(past4) == 0) & past4.lt(q, axis=0)


def stage_e(fwd, trreg) -> dict:
    layer = schema.load_layer("reaction")
    ri = ReactionInputs.load()
    fr = pd.date_range("2023-01-06", fwd[4].index[-1], freq="7D")
    pn = panel(layer, ri, fr)
    W = {k: R.wide(pn, k, "value") for k in ("gnbp_4w", "bngp_4w")}
    D = R.wide(pn, "digest_fx_20d")
    lv = Backbone.load().basket_levels()
    past4 = pd.DataFrame({c: [(R.level_at(lv[c], t - pd.Timedelta(days=1)) - R.level_at(lv[c], t - pd.Timedelta(days=29))) * 1e4
                              for t in fr] for c in CCYS}, index=fr)
    split = pd.Timestamp("2024-11-30")

    def stats(units: list[tuple], h: int) -> dict:
        x = pd.DataFrame([(t, c, fwd[h].at[t, c] if t in fwd[h].index else np.nan) for t, c in units],
                         columns=["t", "c", "r"]).dropna()
        if len(x) < 3:
            return {"mean": float(x["r"].mean()) if len(x) else float("nan"), "se": float("nan"), "n": int(len(x)),
                    "clusters": int(x["t"].nunique()), "x": x}
        m, t, n, G = R.clustered_mean_t(x["r"].to_numpy(), x["t"].to_numpy())
        return {"mean": m, "se": m / t if t else float("nan"), "n": n, "clusters": G, "x": x}

    # control: gnbp == 0 and past 4w basket return in the bottom quintile of the previous 52 Fridays (pooled)
    pooled = past4.stack()
    q = {}
    for t in fr:
        w = past4[(past4.index < t) & (past4.index >= t - pd.Timedelta(weeks=52))].stack()
        q[t] = float(w.quantile(0.2)) if len(w) >= 100 else np.nan
    q = pd.Series(q)
    ctrl_mask = control_mask(W["gnbp_4w"], past4, q)
    del pooled
    control = pick_units(ctrl_mask)
    res = {"episodes": {}, "control": {}}
    for h in (2, 4):
        cs = stats(control, h)
        res["control"][h] = {k: v for k, v in cs.items() if k != "x"}
        res["control"][h]["t_vs_zero"] = cs["mean"] / cs["se"] if cs["se"] and np.isfinite(cs["se"]) else float("nan")
    for fl in ("gnbp_4w", "bngp_4w"):
        eps = pick_units(W[fl] >= 2)
        res["episodes"][fl] = {"n_episodes": len(eps), "by_ccy": pd.Series([c for _, c in eps]).value_counts().to_dict()}
        for h in (2, 4):
            es, cs = stats(eps, h), stats(control, h)
            diff = es["mean"] - cs["mean"]
            t = diff / np.sqrt(es["se"] ** 2 + cs["se"] ** 2) if np.isfinite(es["se"]) and np.isfinite(cs["se"]) else float("nan")
            halves = []
            for lo, hi in ((pd.Timestamp("2023-01-01"), split), (split + pd.Timedelta(days=1), pd.Timestamp("2026-12-31"))):
                e_h = es["x"][(es["x"]["t"] >= lo) & (es["x"]["t"] <= hi)]["r"].mean() if len(es["x"]) else np.nan
                c_h = cs["x"][(cs["x"]["t"] >= lo) & (cs["x"]["t"] <= hi)]["r"].mean() if len(cs["x"]) else np.nan
                halves.append(float(e_h - c_h))
            res["episodes"][fl][h] = {"mean_episode": es["mean"], "n": es["n"], "clusters": es["clusters"],
                                      "mean_control": cs["mean"], "n_control": cs["n"], "diff": diff, "t": t,
                                      "diff_halves": halves}
    # continuous digest, every 4th Friday, by trend / range
    g4 = R.grid_mask(fr, fr[0], 4)
    ics = weekly_ic(D[SIX].reindex(fr)[g4], fwd[4].reindex(fr)[g4][SIX])
    tr = trreg.reindex(ics.index)
    res["digest_fx_20d_4w"] = {"all": grid_stats(ics, 1), "trend": grid_stats(ics[tr == "trend"], 1),
                               "range": grid_stats(ics[tr == "range"], 1)}
    return res


def gates_e(e: dict) -> dict:
    out = {}
    for fl in ("gnbp_4w", "bngp_4w"):
        m = e["episodes"][fl][4]
        crit = {"|diff|>=15bp": bool(np.isfinite(m["diff"]) and abs(m["diff"]) >= 15),
                "|t|>=2.5": bool(np.isfinite(m["t"]) and abs(m["t"]) >= 2.5),
                "same_sign_halves": all(np.isfinite(x) for x in m["diff_halves"]) and
                len({np.sign(x) for x in m["diff_halves"]} | {np.sign(m["diff"])}) == 1}
        out[f"E {fl} @4w"] = {"criteria": crit, "pass": all(crit.values())}
    return out


def stage_g4() -> dict:
    """ECB 14:15 CET fixing vs H.10 noon New York: daily beta_rates_fx and event windows."""
    ecb = pd.read_parquet(ROOT / "data" / "ecb_exr.parquet")
    w = ecb.pivot(index="date", columns="ccy", values="value").dropna()
    w.insert(0, "EUR", 0.0)
    eb = basket(w[[c for c in CCYS if c in w.columns]])
    ri = ReactionInputs.load()
    out = {"daily_beta": {}, "events": {}}
    for c in SIX:
        rel = ri.rel_y2(c, "basket")
        h10 = ri.fx_basket[c] * 1e4
        e = eb[c].diff() * 1e4
        row = {}
        for name, ret in (("h10_noon_ny", h10), ("ecb_1415_cet", e)):
            j = pd.concat([ret.rename("r"), rel.rename("x")], axis=1, join="inner").dropna()
            j = j[j.index >= "2016-01-01"]
            b, t, n = ols(j["x"].to_numpy(), j["r"].to_numpy())
            row[name] = {"beta": b, "t": t, "n": n}
        out["daily_beta"][c] = row
    ev = ri.events_all
    ev = ev[ev["released"] & ev["d2y"].notna() & ev["fx_basket"].notna()]
    for c in SIX:
        s = AlignedSeries(eb[c], "14:15", "Europe/Berlin")
        x = ev[ev["ccy"] == c]
        ecb_mv = []
        for t in x["release_dt"]:
            k = s.first_after(t)
            ecb_mv.append(float((s.vals[k] - s.vals[k - 1]) * 1e4) if 0 < k < len(s.vals) else np.nan)
        x = x.assign(ecb=ecb_mv)
        b1, t1, n1 = ols(x["d2y"].to_numpy(float), x["fx_basket"].to_numpy(float))
        b2, t2, n2 = ols(x["d2y"].to_numpy(float), x["ecb"].to_numpy(float))
        out["events"][c] = {"h10": {"beta": b1, "t": t1, "n": n1}, "ecb": {"beta": b2, "t": t2, "n": n2},
                            "corr_moves": float(x[["fx_basket", "ecb"]].corr().iloc[0, 1])}
    return out


def _f(x, nd=3):
    return "—" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x:.{nd}f}"


def markdown(r: dict) -> str:
    L = [f"# Phase 3 — measurement (pre-registration {r['prereg_commit'][:8]}, attempt #3)", "",
         "Generated by scripts/measure/phase3.py. Raw numbers first; gates at the end.", ""]
    L += ["## C — macro_momentum, cross-sectional (8 currencies, forward basket return)", "",
          "| h | score | IC | t | N_ind | L/S bp | Sharpe | pairs hit | IC 2000-08 | IC 2009-19 | IC 2020-26 |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for h in ["2", "4", "8", "13"]:
        for name, m in r["C"]["total"][h].items():
            sp = r["C"]["subperiods"][h][name]
            L.append(f"| {h} | {name} | {_f(m['IC'])} | {_f(m['t'], 2)} | {m['N_ind']} | {_f(m['ls_bp'], 1)} | "
                     f"{_f(m['ls_sharpe'], 2)} | {_f(m['pairs_hit'])} | " + " | ".join(
                         f"{_f(s['IC'])} ({_f(s['t'], 1)})" for s in sp) + " |")
    L += ["", "By regime v1 (IC (t) [N_ind])", "", "| h | score | calm | liquidity_stress | inflation_shock | growth_scare | stress (all) |",
          "|---|---|---|---|---|---|---|"]
    for h in ["2", "4", "8", "13"]:
        for name in ("composite_eq", "Inflation", "Labor", "Growth", "carry"):
            b = r["C"]["by_regime"][h][name]
            L.append(f"| {h} | {name} | " + " | ".join(
                f"{_f(b.get(k, {}).get('IC'))} ({_f(b.get(k, {}).get('t'), 1)}) [{b.get(k, {}).get('N_ind', 0)}]"
                for k in ("calm", "liquidity_stress", "inflation_shock", "growth_scare", "stress")) + " |")
    L += ["", "Cycle cells (phase × inflation impulse), composite_eq demeaned, pooled grid", "",
          "| h | cell | IC | N | t |", "|---|---|---|---|---|"]
    for h in ["4", "13"]:
        for cell, m in sorted(r["C"]["cycle_cells"][h].items()):
            L.append(f"| {h} | {cell} | {_f(m['IC'])} | {m['N']} | {_f(m['t'], 2)} |")
    L += ["", "Abstention (|composite| < 0.25)", "", "| h | coverage | accuracy | N pred |", "|---|---|---|---|"]
    for h in ["2", "4", "8", "13"]:
        a = r["C"]["abstention"][h]
        L.append(f"| {h} | {_f(a['coverage'])} | {_f(a['accuracy'])} | {a['n_pred']} |")
    if r["C"]["v3_window"]:
        L += ["", "Window of v3 A1 (2023-09 → 2026-10)", "", "| h | score | IC | t | N_ind |", "|---|---|---|---|---|"]
        for h in ["2", "4", "8", "13"]:
            for name, m in r["C"]["v3_window"][h].items():
                L.append(f"| {h} | {name} | {_f(m['IC'])} | {_f(m['t'], 2)} | {m['N_ind']} |")
    D = r["D"]
    if "ic" in D:
        L += ["", "## D — v3 decomposition", "", "| h | component | IC 8 (t) | IC 6 (t) |", "|---|---|---|---|"]
        for h in ["2", "4"]:
            for name, m in D["ic"][h].items():
                L.append(f"| {h} | {name} | {_f(m['8']['IC'])} ({_f(m['8']['t'], 2)}) | {_f(m['6']['IC'])} ({_f(m['6']['t'], 2)}) |")
        L += ["", "Rank correlations (mean weekly cross-sectional Spearman):", ""]
        L += [f"- {k}: {_f(v)}" for k, v in D["rank_corr"].items()]
    E = r["E"]
    L += ["", "## E — attempt #3, good news / bad price", "", "| flag | h | episodes mean bp | N | control mean | N | diff | t | diff halves |",
          "|---|---|---|---|---|---|---|---|---|"]
    for fl in ("gnbp_4w", "bngp_4w"):
        for h in ("2", "4"):
            m = E["episodes"][fl][h]
            L.append(f"| {fl} | {h} | {_f(m['mean_episode'], 1)} | {m['n']} | {_f(m['mean_control'], 1)} | {m['n_control']} | "
                     f"{_f(m['diff'], 1)} | {_f(m['t'], 2)} | {', '.join(_f(x, 1) for x in m['diff_halves'])} |")
    L += ["", "## Gates", ""]
    for k, v in r["gates"].items():
        failed = [c for c, ok in v["criteria"].items() if not ok]
        L.append(f"- {k}: {'PASS' if v['pass'] else 'fail'}" + (f" (fails: {', '.join(failed)})" if failed else ""))
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-json", type=Path, default=ROOT / "data" / "tree_runs" / "phase3_metrics.json")
    ap.add_argument("--out-md", type=Path, default=ROOT / "docs" / "eval" / "phase3.md")
    a = ap.parse_args()
    commit = R.prereg_commit(PREREG)
    yaml.safe_load(PREREG.read_text())
    t0 = time.time()
    tm = {}
    bb, lv, fridays, fwd, base = assemble()
    A, regime, trreg = stage_a(fridays, lv)
    tm["A"] = time.time() - t0
    mi = MacroInputs.load()
    frames, cyc = macro_panel(mi, fridays)
    B = stage_b(mi, cyc)
    tm["B"] = time.time() - t0
    v3 = v3_scores(fridays)
    C = stage_c(frames, fwd, base, regime, cyc, v3)
    tm["C"] = time.time() - t0
    D = stage_d(v3, frames, fwd, regime, trreg)
    E = stage_e(fwd, trreg)
    tm["E"] = time.time() - t0
    G4 = stage_g4()
    gates = {**gates_c(C), **gates_e(E)}
    out = R._clean({"prereg_commit": commit, "A": A, "B": B, "C": C, "D": D, "E": E, "G4": G4, "gates": gates,
                    "seconds": {k: round(v, 1) for k, v in tm.items()} | {"total": round(time.time() - t0, 1)},
                    "fridays": [str(fridays[0].date()), str(fridays[-1].date())]})
    # the panels the registry / stage H reuse
    pd.concat({k: v for k, v in frames.items()}, names=["score", "asof"]).to_parquet(
        ROOT / "data" / "tree_runs" / "macro_momentum_panel.parquet")
    a.out_json.write_text(json.dumps(out, indent=1, sort_keys=True, default=str) + "\n")
    a.out_md.write_text(markdown(out))
    print(f"prereg {commit}; {out['seconds']} -> {a.out_json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
