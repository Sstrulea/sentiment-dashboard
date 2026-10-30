"""Phase 3, stage H: priced vs sustained 2Y moves (pre-registered in prereg_phase3.yml, H_rates).

    python scripts/measure/phase3_h.py [--out-json data/tree_runs/phase3_h_metrics.json]

Target: 2Y change of USD EUR GBP JPY CAD AUD at 4 / 8 / 13 weeks (bp), divided by the std
of daily 2Y changes over the previous 365 days x sqrt(5h). Features as of t (data <= t - 1 day):
macro categories (src.tree.macro), priced slope (2Y - policy), 2Y change 1m / 3m, policy
phase score, equity 3m (NASDAQ to 2016-12-31, SP500 after), VIX, Baa-10y 3m change, own BIS
NEER 3m change. Ridge with sign-constrained coefficients (NNLS on sign-flipped standardised
features, lambda = 0.1 n), walk-forward: first fit 2010-01-01 on 2000-2009, refit each
1 January, training rows = targets that ended before the refit date. Refuses to run with
an uncommitted pre-registration."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import nnls

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "measure"))

import reaction_v0 as R  # noqa: E402
from src.tree.backbone import Backbone  # noqa: E402
from src.tree.data import Inputs  # noqa: E402
from src.tree.macro import MacroInputs  # noqa: E402
from src.tree.reaction import ols  # noqa: E402
from src.tree.regimes import REGIME_INPUTS, RegimeV1  # noqa: E402

PREREG = ROOT / "scripts" / "measure" / "prereg_phase3.yml"
BANKS = ["USD", "EUR", "GBP", "JPY", "CAD", "AUD"]
SIGNS = {"Inflation": 1, "Labor": 1, "Growth": 1, "slope": -1, "d2y_1m": 1, "d2y_3m": 1, "phase": 1,
         "eq_3m": 1, "vix": -1, "baa_d3m": -1, "neer_3m": -1}
PHASE = {"tightening": 1, "hold_top": 0, "hold_bottom": 0, "easing": -1}
HS = [4, 8, 13]
LAMBDA = 0.1


def at(s: pd.Series, t) -> float:
    return R.level_at(s, pd.Timestamp(t))


def features(fridays) -> pd.DataFrame:
    bb = Backbone.load()
    mi = MacroInputs.load()
    ri = Inputs.load(REGIME_INPUTS)
    neer = pd.read_parquet(ROOT / "data" / "bis_neer.parquet")
    neer = {c: pd.Series(g["value"].to_numpy(float), index=pd.DatetimeIndex(g["date"])) for c, g in neer.groupby("ccy")}
    full = {n: ri.full(n) for n in ("NASDAQCOM", "SP500", "VIXCLS", "BAA10Y")}
    rows = []
    for t in fridays:
        d1 = t - pd.Timedelta(days=1)
        eq_s = full["NASDAQCOM"] if t <= pd.Timestamp("2016-12-31") else full["SP500"]
        eq3 = (at(eq_s, d1) / at(eq_s, d1 - pd.Timedelta(days=91)) - 1) * 100
        vix = at(full["VIXCLS"], d1)
        baa = (at(full["BAA10Y"], d1) - at(full["BAA10Y"], d1 - pd.Timedelta(days=91))) * 100
        for c in BANKS:
            y2, pol = bb.by[f"Y2_{c}"], bb.by[f"POL_{c}"]
            m = mi.momentum(c, t)["categories"]
            ph = mi.phase(c, t)
            n = neer.get(c)
            rows.append({"asof": t, "ccy": c, **{k: m[k] for k in ("Inflation", "Labor", "Growth")},
                         "slope": (at(y2, d1) - at(pol, d1)) * 100,
                         "d2y_1m": (at(y2, d1) - at(y2, d1 - pd.Timedelta(days=30))) * 100,
                         "d2y_3m": (at(y2, d1) - at(y2, d1 - pd.Timedelta(days=91))) * 100,
                         "phase": PHASE.get(ph) if ph else np.nan, "eq_3m": eq3, "vix": vix, "baa_d3m": baa,
                         "neer_3m": (np.log(at(n, d1)) - np.log(at(n, d1 - pd.Timedelta(days=91)))) * 100
                         if n is not None else np.nan})
    return pd.DataFrame(rows)


def targets(fridays) -> pd.DataFrame:
    bb = Backbone.load()
    rows = []
    for c in BANKS:
        y2 = bb.by[f"Y2_{c}"].dropna()
        dd = y2.diff() * 100
        for t in fridays:
            sd = dd[(dd.index < t) & (dd.index >= t - pd.Timedelta(days=365))].std(ddof=1)
            r = {"asof": t, "ccy": c}
            for h in HS:
                ch = R.forward(y2, pd.DatetimeIndex([t]), h, 100.0).iloc[0]
                r[f"y{h}"] = ch / (sd * np.sqrt(5 * h)) if sd and np.isfinite(sd) and sd > 0 else np.nan
            rows.append(r)
    return pd.DataFrame(rows)


def fit(X: np.ndarray, y: np.ndarray, lam: float) -> np.ndarray:
    """min ||y - X b||^2 + lam ||b||^2, b >= 0."""
    p = X.shape[1]
    A = np.vstack([X, np.sqrt(lam) * np.eye(p)])
    bvec = np.concatenate([y, np.zeros(p)])
    return nnls(A, bvec, maxiter=50 * p)[0]


def walk_forward(df: pd.DataFrame, h: int) -> tuple[pd.DataFrame, dict]:
    feats = list(SIGNS)
    out, coefs = [], {}
    years = range(2010, df["asof"].dt.year.max() + 1)
    for yr in years:
        refit = pd.Timestamp(f"{yr}-01-01")
        tr = df[(df["asof"] + pd.Timedelta(days=7 * h) < refit) & df[f"y{h}"].notna()]
        te = df[(df["asof"] >= refit) & (df["asof"] < pd.Timestamp(f"{yr + 1}-01-01"))]
        if len(tr) < 200 or te.empty:
            continue
        mu, sd = tr[feats].mean(), tr[feats].std(ddof=1).replace(0, np.nan)
        Xtr = ((tr[feats] - mu) / sd).fillna(0.0) * pd.Series(SIGNS)
        ytr = tr[f"y{h}"] - tr[f"y{h}"].mean()
        b = fit(Xtr.to_numpy(), ytr.to_numpy(), LAMBDA * len(tr))
        Xte = ((te[feats] - mu) / sd).fillna(0.0) * pd.Series(SIGNS)
        o = te[["asof", "ccy", f"y{h}"]].copy()
        o["pred"] = Xte.to_numpy() @ b + tr[f"y{h}"].mean()
        o["refit"] = yr
        out.append(o)
        coefs[yr] = dict(zip(feats, b * pd.Series(SIGNS).to_numpy()))
    return (pd.concat(out) if out else pd.DataFrame()), coefs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-json", type=Path, default=ROOT / "data" / "tree_runs" / "phase3_h_metrics.json")
    a = ap.parse_args()
    commit = R.prereg_commit(PREREG)
    t0 = time.time()
    lv = Backbone.load().basket_levels()
    fridays = pd.date_range("2000-01-07", lv.index[-1], freq="7D")
    df = features(fridays).merge(targets(fridays), on=["asof", "ccy"])
    res = {"prereg_commit": commit, "rows": int(len(df)), "by_h": {}}
    rv = RegimeV1()
    regime = pd.Series({t: rv.label(t) for t in fridays})
    ri_daily = None
    for h in HS:
        pr, coefs = walk_forward(df, h)
        if pr.empty:
            continue
        y = pr[f"y{h}"]
        ok = y.notna()
        p = pr[ok]
        ic = R.spearman(p["pred"], p[f"y{h}"])
        g = R.grid_mask(pd.DatetimeIndex(p["asof"]), pd.Timestamp("2010-01-01"), h)
        ic_g = R.spearman(p["pred"][g], p[f"y{h}"][g])
        per = {c: R.spearman(p.loc[p.ccy == c, "pred"], p.loc[p.ccy == c, f"y{h}"]) for c in BANKS}
        res["by_h"][h] = {"IC": ic, "IC_grid": ic_g, "t": R.t_of_ic(ic_g, int(g.sum())), "N": int(len(p)),
                          "N_ind": int(g.sum()), "per_bank": per,
                          "banks_positive": int(sum(1 for v in per.values() if np.isfinite(v) and v > 0)),
                          "coef_last": coefs.get(max(coefs)) if coefs else None}
        # direct vs two-stage, FX basket, 6 currencies (h = 8, 13)
        if h in (8, 13):
            fwd = pd.DataFrame({c: R.forward(lv[c], fridays, h, 1e4) for c in BANKS})
            mi = MacroInputs.load()
            direct = pd.DataFrame({c: [mi.momentum(c, t)["composite_eq"] for t in fridays] for c in BANKS},
                                  index=fridays, dtype=float)
            P = p.pivot(index="asof", columns="ccy", values="pred").reindex(columns=BANKS)
            rel = P.sub(P.mean(axis=1), axis=0)
            if ri_daily is None:
                from src.tree.reaction import ReactionInputs
                ri_daily = ReactionInputs.load()
            beta = {}
            for c in BANKS:
                f = pd.concat([(ri_daily.fx_basket[c] * 1e4).rename("r"), ri_daily.rel_y2(c, "basket").rename("x")],
                              axis=1, join="inner").dropna()
                beta[c] = {}
                for yr in sorted(P.index.year.unique()):
                    tr = f[f.index < pd.Timestamp(f"{yr}-01-01")]
                    reg = pd.Series({d: rv.label(d) for d in pd.date_range(tr.index[0], tr.index[-1], freq="7D")})
                    lab = reg.reindex(tr.index, method="ffill")
                    beta[c][yr] = {r: ols(tr.loc[lab == r, "x"].to_numpy(), tr.loc[lab == r, "r"].to_numpy())[0]
                                   for r in lab.dropna().unique()} | {"all": ols(tr["x"].to_numpy(), tr["r"].to_numpy())[0]}
            two = rel.copy()
            for t in two.index:
                r_t = regime.get(t, "calm")
                for c in BANKS:
                    b = beta[c][t.year]
                    two.at[t, c] = rel.at[t, c] * (b.get(r_t) if np.isfinite(b.get(r_t, np.nan)) else b["all"])
            w0 = pd.Timestamp("2010-01-01")
            fw = fwd[fwd.index >= w0]

            def gstats(S):
                ics = pd.Series([R.spearman(S.loc[t], fw.loc[t]) for t in fw.index if t in S.index],
                                index=[t for t in fw.index if t in S.index]).dropna()
                gg = ics[R.grid_mask(pd.DatetimeIndex(ics.index), w0, h)]
                return {"IC": float(gg.mean()), "t": float(gg.mean() / (gg.std(ddof=1) / np.sqrt(len(gg)))), "N_ind": int(len(gg))}
            res["by_h"][h]["fx_direct_macro"] = gstats(direct.reindex(fw.index))
            res["by_h"][h]["fx_two_stage"] = gstats(two.reindex(fw.index))
    gates = {}
    for h in (8, 13):
        m = res["by_h"].get(h)
        if not m:
            continue
        crit = {"IC>=0.08": bool(np.isfinite(m["IC"]) and m["IC"] >= 0.08), "t>=2.5": bool(np.isfinite(m["t"]) and m["t"] >= 2.5),
                ">=5/6 banks IC>0": m["banks_positive"] >= 5}
        gates[f"H @{h}w"] = {"criteria": crit, "pass": all(crit.values())}
    res["gates"] = gates
    res["seconds"] = round(time.time() - t0, 1)
    out = R._clean(res)
    a.out_json.write_text(json.dumps(out, indent=1, sort_keys=True, default=str) + "\n")
    print(json.dumps({k: out[k] for k in ("gates", "seconds")}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
