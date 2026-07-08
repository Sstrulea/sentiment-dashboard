"""V5a — COMPOSITE-weight ablation (READ-ONLY). Internal pillar scoring is NOT touched
(see memory fundamental-pillar-no-edge); variants differ ONLY by composition weights.
Point-in-time replay of ALL components (trend walk, COT sentiment w/ release lag, FRED
rates/realyield/liquidity, FF surprise), then M1-M3 (demeaned S) + anti-artifact battery.
Nothing adopted. Run: SCRATCH=<dir> python -m scripts.v5a  (caches the replay parquet)."""
from __future__ import annotations
import copy, os
from pathlib import Path
import numpy as np, pandas as pd, yaml

from src.economic_compute import build_payload
from src.crossasset_compute import compute_crossasset_scores
from src.rate_compute import compute_rate_scores
from src.realyield_compute import compute_realyield_score
from src.liquidity_compute import compute_liquidity_score
from src.ff_scoring import build_matcher, to_scoring_frame
from src.trend_score import score_all as trend_score_all
from src.cot_score import score_currencies, score_metals, load_currencies_history, load_metals_history, DATE_COL
from src.fundamental_v4_replay import attach_trading_day_returns

ROOT = Path(__file__).resolve().parents[1]
SCRATCH = Path(os.environ.get("SCRATCH", ROOT / "data" / ".v5a_cache"))
CSV = ROOT / "data" / "v5a_ablation.csv"
HZ = [5, 15, 30]
BUCKETS = ["Very Bearish", "Bearish", "Bullish", "Very Bullish"]
BULL, BEAR = {"Bullish", "Very Bullish"}, {"Very Bearish", "Bearish"}
PROFILE_REP = ["SP500", "DAX", "NIKKEI", "FTSE100", "GOLD"]
COT_RELEASE_LAG_DAYS = 3   # ASSUMPTION: no release-date col in COT parquet → report(Tue)+3 ≈ publish(Fri)

# variant spec: surprise-category weight multiplier + which non-surprise factors are ON
COMPOSITE_VARIANTS = {
    "V5.T":    dict(surprise=0.0, monetary=False, sentiment=False, trend=True,  rates_liq=False),
    "V5.TS":   dict(surprise=0.0, monetary=False, sentiment=True,  trend=True,  rates_liq=False),
    "V5.NOF":  dict(surprise=0.0, monetary=True,  sentiment=True,  trend=True,  rates_liq=True),
    "V5.HALF": dict(surprise=0.5, monetary=True,  sentiment=True,  trend=True,  rates_liq=True),
    "V5.FULL": dict(surprise=1.0, monetary=True,  sentiment=True,  trend=True,  rates_liq=True),
}
SURPRISE_CATS = ["growth", "inflation", "labour"]


def variant_ind_cfg(base, mult):
    d = copy.deepcopy(base)
    for c in SURPRISE_CATS:
        if c in d.get("categories", {}):
            d["categories"][c]["weight"] = float(d["categories"][c].get("weight", 1.0)) * mult
    return d


def variant_ca_cfg(base, spec):
    d = copy.deepcopy(base)
    for sym, icfg in d.get("instruments", {}).items():
        f = icfg.get("factors", {})
        for c in SURPRISE_CATS:
            if c in f:
                if spec["surprise"] == 0.0:
                    del f[c]
                else:
                    f[c]["weight"] = float(f[c].get("weight", 1.0)) * spec["surprise"]
        if not spec["rates_liq"] and "rates" in f:   # rates factor bundles monetary(2y)+realyield+liquidity
            del f["rates"]
        if not spec["sentiment"] and "sentiment" in f:
            del f["sentiment"]
        if not spec["trend"] and "trend" in f:
            del f["trend"]
    return d


def cot_cells_asof(cur_hist, met_hist, as_of):
    """Point-in-time COT cells using PUBLICATION date = report_date + 3d (documented approx)."""
    cutoff = as_of - pd.Timedelta(days=COT_RELEASE_LAG_DAYS)
    cur = cur_hist[cur_hist[DATE_COL] <= cutoff]
    met = met_hist[met_hist[DATE_COL] <= cutoff]
    ccy = {r["symbol"]: int(r["cell"]) for _, r in score_currencies(cur).iterrows()} if len(cur) else {}
    metals = {r["symbol"]: int(r["cell"]) for _, r in score_metals(met).iterrows()} if len(met) else {}
    return ccy, metals


def run_replay(step=1):
    cal = to_scoring_frame(pd.read_parquet(ROOT / "data" / "economic_calendar_ff.parquet"), build_matcher())
    cal["release_dt"] = pd.to_datetime(cal["release_dt"])
    rates = pd.read_parquet(ROOT / "data" / "rates.parquet"); rates["date"] = pd.to_datetime(rates["date"])
    realy = pd.read_parquet(ROOT / "data" / "real_yields.parquet")
    netliq = pd.read_parquet(ROOT / "data" / "net_liquidity.parquet")
    ind_base = yaml.safe_load(open(ROOT / "data" / "economic_indicators.yaml"))
    inst = yaml.safe_load(open(ROOT / "data" / "economic_instruments.yaml"))
    ca_base = yaml.safe_load(open(ROOT / "data" / "crossasset_instruments.yaml"))
    cur_hist = load_currencies_history(); cur_hist[DATE_COL] = pd.to_datetime(cur_hist[DATE_COL])
    met_hist = load_metals_history(); met_hist[DATE_COL] = pd.to_datetime(met_hist[DATE_COL])

    cal_actual = cal[cal["actual"].notna()]
    first = (cal["release_dt"].min() + pd.Timedelta(days=120)).normalize()
    last = cal_actual["release_dt"].max().normalize()
    asofs = pd.bdate_range(first, last)[::step]

    ind_cfgs = {v: variant_ind_cfg(ind_base, spec["surprise"]) for v, spec in COMPOSITE_VARIANTS.items()}
    ca_cfgs = {v: variant_ca_cfg(ca_base, spec) for v, spec in COMPOSITE_VARIANTS.items()}
    rows, cov = [], []
    for i, as_of in enumerate(asofs):
        cal_slice = cal[cal["release_dt"] <= as_of]
        rs = compute_rate_scores(rates[rates["date"] <= as_of], as_of=as_of.date())
        _ry = compute_realyield_score(realy[pd.to_datetime(realy["date"]) <= as_of], as_of=as_of.date())
        _lq = compute_liquidity_score(netliq[pd.to_datetime(netliq["date"]) <= as_of], as_of=as_of.date())
        tc = {k: int(v["trend_cell"]) for k, v in trend_score_all(server_today=as_of).items()
              if isinstance(v, dict) and v.get("trend_cell") is not None}
        ccy_cot, metal_cot = cot_cells_asof(cur_hist, met_hist, as_of)
        cov.append({"as_of": as_of, "trend_n": len(tc), "cot_ccy_n": len(ccy_cot),
                    "cot_metal_n": len(metal_cot), "ry": _ry is not None, "lq": _lq is not None})
        for v, spec in COMPOSITE_VARIANTS.items():
            rsv = rs if spec["monetary"] else None
            scv = (ccy_cot if spec["sentiment"] else None)
            tcv = (tc if spec["trend"] else None)
            pay = build_payload(cal_slice, ind_cfgs[v], inst, as_of=as_of, rate_scores=rsv,
                                sentiment_cells=scv, trend_cells=tcv)
            for ip in pay["instruments"]:
                rows.append({"as_of": as_of, "variant": v, "symbol": ip["symbol"],
                             "cls": "fx" if ip["type"] == "fx" else "single",
                             "score": ip["score"], "bias": ip["bias"]})
            cats = {c: card.get("categories", {}) for c, card in pay["currencies"].items()}
            senti_xa = ({**metal_cot} if spec["sentiment"] else None)   # metals COT; US-index P/C excluded (see docs)
            ca = compute_crossasset_scores(cats, _ry if spec["rates_liq"] else None, ca_cfgs[v],
                                           liquidity_score=_lq if spec["rates_liq"] else None,
                                           sentiment_by_symbol=senti_xa,
                                           trend_by_symbol=(tc if spec["trend"] else None))
            for sym, r in ca.items():
                rows.append({"as_of": as_of, "variant": v, "symbol": sym, "cls": "cross-asset",
                             "score": r.get("score_precise", 0.0), "bias": r.get("bias_label")})
    df = pd.DataFrame(rows); df["as_of"] = pd.to_datetime(df["as_of"])
    return df, pd.DataFrame(cov)


def _load_or_gen():
    SCRATCH.mkdir(parents=True, exist_ok=True)
    p, pc = SCRATCH / "v5a.parquet", SCRATCH / "v5a_cov.parquet"
    if not (p.exists() and pc.exists()):
        from src.fundamental_v4_replay import _forward_returns
        df, cov = run_replay(step=1)
        df = attach_trading_day_returns(df, _forward_returns(), HZ)
        df.to_parquet(p); cov.to_parquet(pc)
    return pd.read_parquet(p), pd.read_parquet(pc)


# ============================ METRICS ============================
VARIANTS = list(COMPOSITE_VARIANTS.keys())


def nn_of(df):
    return df[df["bias"].isin(BUCKETS)].copy()


def demean(df):
    df = df.copy()
    for H in HZ:
        m = df.groupby("symbol")[f"r{H}"].transform("mean")
        df[f"d{H}"] = df[f"r{H}"] - m
    return df


def base_bull(df, H):
    d = df[df[f"r{H}"].notna()]
    return {s: (g[f"r{H}"] > 0).mean() for s, g in d.groupby("symbol")}


def agg_excess(sub, bb, H):
    sub = sub[sub[f"r{H}"].notna()]
    if len(sub) < 20:
        return np.nan, len(sub)
    up = sub["bias"].isin(BULL)
    hit = np.where(up, sub[f"r{H}"] > 0, sub[f"r{H}"] < 0)
    bench = np.where(up, sub["symbol"].map(lambda s: bb.get(s, .5)),
                     sub["symbol"].map(lambda s: 1 - bb.get(s, .5)))
    return 100 * (hit.mean() - bench.mean()), len(sub)


def spread_S(sub, H, col="d"):
    sub = sub[sub[f"{col}{H}"].notna()]
    bull = sub[sub["bias"].isin(BULL)][f"{col}{H}"]; bear = sub[sub["bias"].isin(BEAR)][f"{col}{H}"]
    if len(bull) < 5 or len(bear) < 5:
        return np.nan
    return 1e4 * (bull.mean() - bear.mean())


def disjoint(df, H):
    aos = np.sort(df["as_of"].unique())
    return df[df["as_of"].isin(set(aos[::H]))]


def coverage_table(cov):
    cov = cov.copy(); cov["yr"] = pd.to_datetime(cov["as_of"]).dt.year
    print("\n[COVERAGE] per year — as-ofs with each component present")
    print(f"  {'year':6}{'n_asof':>8}{'trend≥1':>9}{'trend=full':>11}{'cot':>6}{'ry':>6}{'lq':>6}")
    for y, g in cov.groupby("yr"):
        print(f"  {y:<6}{len(g):>8}{100*(g.trend_n>0).mean():>8.0f}%{100*(g.trend_n>=30).mean():>10.0f}%"
              f"{100*(g.cot_ccy_n>0).mean():>5.0f}%{100*g.ry.mean():>5.0f}%{100*g.lq.mean():>5.0f}%")


def metrics_block(df, label, disjoint_mode):
    print(f"\n[{label}] M1 excess / M2 S_demeaned per variant × class × H"
          + ("  (DISJOINT step=H — PRIMARY)" if disjoint_mode else "  (step 1 — descriptive)"))
    out = {}
    for cls in ["fx", "cross-asset"]:
        print(f"  --- {cls} ---")
        print(f"  {'variant':9}" + "".join(f"H{H}exc{'':>2}H{H}S{'':>3}" for H in HZ))
        for v in VARIANTS:
            dv = df[df.variant == v]
            if disjoint_mode:
                cells = []
                for H in HZ:
                    dj = nn_of(demean(disjoint(df[df.variant == v], H)))
                    bb = base_bull(disjoint(df[df.variant == v], H), H)
                    e, _ = agg_excess(dj[dj.cls == cls], bb, H); S = spread_S(dj[dj.cls == cls], H)
                    out[(v, cls, H)] = (e, S)
                    cells.append(f"{e:>6.1f}{S:>8.0f}")
            else:
                dn = nn_of(demean(dv)); bb = {H: base_bull(dv, H) for H in HZ}
                cells = []
                for H in HZ:
                    e, _ = agg_excess(dn[dn.cls == cls], bb[H], H); S = spread_S(dn[dn.cls == cls], H)
                    cells.append(f"{e:>6.1f}{S:>8.0f}")
            print(f"  {v:9}" + "".join(cells))
    return out


def selectivity(df):
    print("\n[SELECTIVITY] % non-Neutral per variant × class")
    print(f"  {'variant':9}{'fx':>8}{'cross-asset':>14}")
    for v in VARIANTS:
        dv = df[df.variant == v]
        pf = 100 * (dv[dv.cls == "fx"]["bias"].isin(BUCKETS)).mean()
        pc = 100 * (dv[dv.cls == "cross-asset"]["bias"].isin(BUCKETS)).mean()
        print(f"  {v:9}{pf:>8.1f}{pc:>14.1f}")


def battery(df, variant):
    print(f"\n[BATTERY] {variant} cross-asset (demeaned S)")
    ca = nn_of(demean(df[df.variant == variant]))
    ca = ca[ca.cls == "cross-asset"]
    rng = np.random.default_rng(7)
    for H in [15, 30]:
        # leave-top-out
        sub = ca[ca[f"d{H}"].notna()].sort_values(["symbol", "as_of"]).copy()
        if len(sub) < 20:
            print(f"  H={H}: too few obs ({len(sub)}) — battery N/A"); continue
        sub["grp"] = np.where(sub["bias"].isin(BULL), 1, -1)
        chg = (sub["symbol"] != sub["symbol"].shift()) | (sub["grp"] != sub["grp"].shift())
        sub["epi"] = chg.cumsum()
        contrib = sub.groupby("epi").apply(lambda g: g["grp"].iloc[0] * g[f"d{H}"].sum(), include_groups=False)
        loo = sub[~sub["epi"].isin(contrib.sort_values(ascending=False).head(3).index)]
        Sloo = spread_S(loo, H)
        # placebo permute
        perm = ca.copy()
        perm["bias"] = perm.groupby("symbol")["bias"].transform(lambda s: rng.permutation(s.values))
        Sp = spread_S(perm, H)
        rep = spread_S(ca[ca.symbol.isin(PROFILE_REP)], H)
        Sr = spread_S(ca, H)
        ok = abs(Sp) < 0.25 * abs(Sr) if Sr and not np.isnan(Sr) else False
        print(f"  H={H}: S_real={Sr:.0f} | S_loo={Sloo:.0f} (>0? {Sloo>0}) | "
              f"S_placebo={Sp:.0f} (<25%? {ok}) | S_dedup_rep={rep:.0f}")


def main():
    df, cov = _load_or_gen()
    df["as_of"] = pd.to_datetime(df["as_of"])
    print(f"V5a replay: {len(df)} rows, {df.as_of.nunique()} as-ofs, variants {VARIANTS}")
    coverage_table(cov)
    selectivity(df)
    metrics_block(df, "STEP1", disjoint_mode=False)
    dj = metrics_block(df, "DISJOINT", disjoint_mode=True)
    # M3 consistency (demeaned S sign by sub-period), disjoint
    print("\n[M3] demeaned-S sign by sub-period (fx 2024/25/26; x-asset 2025/26), DISJOINT")
    for v in VARIANTS:
        d = nn_of(demean(df[df.variant == v])); d["yr"] = d["as_of"].dt.year
        line = []
        for cls, yrs in [("fx", [2024, 2025, 2026]), ("cross-asset", [2025, 2026])]:
            for H in [15, 30]:
                dj_v = nn_of(demean(disjoint(df[df.variant == v], H))); dj_v["yr"] = dj_v["as_of"].dt.year
                vals = [spread_S(dj_v[(dj_v.cls == cls) & (dj_v.yr == y)], H) for y in yrs]
                line.append(f"{cls[:2]}H{H}[" + ",".join("n/a" if np.isnan(x) else f"{x:+.0f}" for x in vals) + "]")
        print(f"  {v:9} " + " ".join(line))
    battery(df, "V5.FULL")
    # save CSV
    keep = ["as_of", "variant", "symbol", "cls", "score", "bias", "r5", "r15", "r30"]
    nn_of(df)[keep].to_csv(CSV, index=False)
    print(f"\nwrote {CSV} ({len(nn_of(df))} non-Neutral rows)")


if __name__ == "__main__":
    main()
