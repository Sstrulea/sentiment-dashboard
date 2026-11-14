"""Measure the reaction layer v0 against scripts/measure/prereg_reaction_v0.yml (attempt #2).

    python scripts/measure/reaction_v0.py [--out-json data/tree_runs/reaction_v0_metrics.json]
                                          [--out-md docs/eval/reaction-v0.md]

Refuses to run while the pre-registration has uncommitted changes. Runs the
weekly walk-forward of the layer itself (offline), then: descriptive tables
(backbone coverage, events, betas, polarity audit, beta_rates_fx by regime,
flags by year) and the pre-registered tests H1-H4 with the cross-sectional
harness and its baselines. Raw numbers first; verdicts at the end."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from scipy.stats import spearmanr

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.tree import schema  # noqa: E402
from src.tree.data import Inputs  # noqa: E402
from src.tree.engine import Engine, classify  # noqa: E402
from src.tree.reaction import ReactionInputs, ols, panel, snapshot  # noqa: E402

PREREG = ROOT / "scripts" / "measure" / "prereg_reaction_v0.yml"
V3_SCORES = Path.home() / ".cache" / "macro-tournament" / "scores_main.parquet"
CCYS = ["USD", "EUR", "GBP", "JPY", "CAD", "AUD", "NZD", "CHF"]
TOL_DAYS = 4
SEED = 20261030


def prereg_commit(path: Path = PREREG) -> str:
    rel = str(path.relative_to(ROOT))
    tracked = subprocess.run(["git", "ls-files", "--error-unmatch", rel], cwd=ROOT,
                             capture_output=True).returncode == 0
    dirty = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", rel], cwd=ROOT).returncode != 0
    if not tracked or dirty:
        raise SystemExit(f"{rel} is not committed as-is: commit the pre-registration first")
    return subprocess.run(["git", "log", "-1", "--format=%H", "--", rel], cwd=ROOT,
                          capture_output=True, text=True).stdout.strip()


# ---------------------------------------------------------------------------
# Forward values
# ---------------------------------------------------------------------------

def level_at(s: pd.Series, t: pd.Timestamp) -> float:
    """Last value on or before t, NaN when older than TOL_DAYS or t is past the data."""
    s = s.dropna()
    if s.empty or t > s.index[-1] + pd.Timedelta(days=TOL_DAYS):
        return float("nan")
    i = s.index.searchsorted(t, side="right") - 1
    if i < 0 or (t - s.index[i]).days > TOL_DAYS:
        return float("nan")
    return float(s.iloc[i])


def forward(s: pd.Series, asofs, h: int, scale: float) -> pd.Series:
    """(s[t + 7h] - s[t]) x scale; NaN when the end is past the last observation."""
    last = s.dropna().index[-1]
    out = []
    for t in asofs:
        e = t + pd.Timedelta(days=7 * h)
        out.append(np.nan if e > last else (level_at(s, e) - level_at(s, t)) * scale)
    return pd.Series(out, index=asofs)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def spearman(a, b) -> float:
    a, b = np.asarray(a, float), np.asarray(b, float)
    m = np.isfinite(a) & np.isfinite(b)
    if m.sum() < 3 or np.unique(a[m]).size < 2 or np.unique(b[m]).size < 2:
        return float("nan")
    return float(spearmanr(a[m], b[m]).correlation)


def t_of_ic(ic: float, n: int) -> float:
    return float(ic * np.sqrt((n - 2) / (1 - ic * ic))) if np.isfinite(ic) and n > 2 and abs(ic) < 1 else float("nan")


def grid_mask(asofs: pd.DatetimeIndex, start: pd.Timestamp, h: int) -> np.ndarray:
    k = ((asofs - start).days // 7).to_numpy()
    return (k % h) == 0


def halves(prereg) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    h = prereg["harness"]["halves"]
    return [(pd.Timestamp(h["first"][0]), pd.Timestamp(h["first"][1])),
            (pd.Timestamp(h["second"][0]), pd.Timestamp("2100-01-01"))]


def cs_metrics(S: pd.DataFrame, R: pd.DataFrame, h: int, start: pd.Timestamp, prereg, cost_bp: float = 3.0,
               min_cs: int = 5) -> dict:
    """S, R: Friday x currency (score, forward return bp)."""
    common = [c for c in S.columns if c in R.columns]
    S, R = S[common], R[common]
    ok = (S.notna() & R.notna()).sum(axis=1) >= min_cs
    ics = pd.Series([spearman(S.loc[t], R.loc[t]) for t in S.index], index=S.index)[ok].dropna()
    g = grid_mask(ics.index, start, h)
    ics_no = ics[g]
    n = len(ics_no)
    mean_no = float(ics_no.mean()) if n else float("nan")
    t = float(mean_no / (ics_no.std(ddof=1) / np.sqrt(n))) if n > 2 and ics_no.std(ddof=1) > 0 else float("nan")
    hv = []
    for lo, hi in halves(prereg):
        x = ics[(ics.index >= lo) & (ics.index <= hi)]
        xg = x[grid_mask(x.index, start, h)]
        hv.append(float(x.mean()) if len(xg) >= 10 else float("nan"))
    # long-short on the non-overlapping grid
    weeks = [w for w in S.index[ok] if grid_mask(pd.DatetimeIndex([w]), start, h)[0]]
    prev = pd.Series(0.0, index=common)
    pnl = []
    for w in weeks:
        s = S.loc[w].dropna()
        s = s[R.loc[w, s.index].notna()]
        if len(s) < min_cs:
            continue
        rk = s.sort_values()
        wt = pd.Series(0.0, index=common)
        wt[rk.index[-2:]] = 0.5
        wt[rk.index[:2]] = -0.5
        gross = float((wt * R.loc[w].fillna(0.0)).sum())
        cost = cost_bp * float((wt - prev).abs().sum())
        pnl.append(gross - cost)
        prev = wt
    pnl = np.array(pnl)
    cum = np.cumsum(pnl) if len(pnl) else np.array([0.0])
    dd = float(np.max(np.maximum.accumulate(cum) - cum)) if len(pnl) else float("nan")
    sharpe = float(pnl.mean() / pnl.std(ddof=1) * np.sqrt(52 / h)) if len(pnl) > 2 and pnl.std(ddof=1) > 0 else float("nan")
    # pairs + abstention on the grid
    hits = tot = cov_n = cov_d = acc = 0
    for w in weeks:
        s, r = S.loc[w], R.loc[w]
        for a, b in combinations(common, 2):
            if pd.notna(s[a]) and pd.notna(s[b]) and pd.notna(r[a]) and pd.notna(r[b]) and s[a] != s[b] and r[a] != r[b]:
                tot += 1
                hits += np.sign(s[a] - s[b]) == np.sign(r[a] - r[b])
        x = s[s.notna() & r.notna()]
        if len(x) >= min_cs and x.std(ddof=1) > 0:
            cz = (x - x.mean()) / x.std(ddof=1)
            for c in x.index:
                cov_d += 1
                if abs(cz[c]) >= 0.5:
                    cov_n += 1
                    acc += np.sign(cz[c]) == np.sign(r[c])
    return {"IC": mean_no, "IC_all": float(ics.mean()) if len(ics) else float("nan"), "t": t,
            "N_weeks": int(len(ics)), "N_ind": n, "IC_h1": hv[0], "IC_h2": hv[1],
            "ls_bp": float(pnl.mean()) if len(pnl) else float("nan"), "ls_sharpe": sharpe, "ls_maxdd_bp": dd,
            "ls_n": int(len(pnl)), "pairs_hit": hits / tot if tot else float("nan"), "pairs_n": int(tot),
            "coverage": cov_n / cov_d if cov_d else float("nan"), "accuracy": acc / cov_n if cov_n else float("nan")}


def clustered_mean_t(x: np.ndarray, clusters: np.ndarray, mu0: float = 0.0) -> tuple[float, float, int, int]:
    """mean, t of (mean - mu0) with SE clustered on `clusters`, n, n_clusters."""
    x = np.asarray(x, float)
    n = len(x)
    if n < 3:
        return float("nan"), float("nan"), n, len(set(clusters))
    m = x.mean()
    e = pd.Series(x - m).groupby(np.asarray(clusters)).sum().to_numpy()
    G = len(e)
    se = np.sqrt((e ** 2).sum()) / n * np.sqrt(G / (G - 1)) if G > 1 else float("nan")
    return float(m), float((m - mu0) / se) if se and se > 0 else float("nan"), n, G


# ---------------------------------------------------------------------------
# Descriptive tables
# ---------------------------------------------------------------------------

def coverage_table(ri: ReactionInputs) -> list[dict]:
    f = ri.bb.frame
    out = []
    for s, g in f.groupby("series"):
        d = g["date"].sort_values()
        gaps = d.diff().dt.days
        out.append({"series": s, "code": " | ".join(sorted(g["code"].unique())), "first": str(d.min().date()),
                    "last": str(d.max().date()), "n": int(len(d)),
                    "gaps_gt7d_since_2022": int((gaps[d >= "2022-01-01"] > 7).sum()),
                    "max_gap_since_2022": int(gaps[d >= "2022-01-01"].max()) if (d >= "2022-01-01").sum() > 1 else None,
                    "as_of_rule": "M+2 first day + 10d" if s == "Y2_CHF" else "date + 1d"})
    return out


def events_table(ev: pd.DataFrame) -> list[dict]:
    r = ev[ev["released"]]
    out = []
    for c, g in r.groupby("ccy"):
        out.append({"ccy": c, "events": int(len(g)), "with_z": int(g["z_raw"].notna().sum()),
                    "pct_d2y": float(g["d2y"].notna().mean()), "pct_fx": float(g["fx_basket"].notna().mean()),
                    "first": str(g["release_dt"].min().date()), "last": str(g["release_dt"].max().date()),
                    "by_category": g["category"].value_counts().to_dict()})
    return out


def betas_table(snap: dict) -> tuple[list[dict], list[dict]]:
    bet, pol = [], []
    for c, nodes in snap["currencies"].items():
        b = nodes["beta_data_rates"]
        if b["quality"] != "ok":
            bet.append({"ccy": c, "missing": b["meta"].get("reason")})
            continue
        m = b["meta"]
        by = {}
        for i, v in m["indicators"].items():
            by.setdefault(v["category"], []).append(v)
            raw, se = v["raw"], v["se"]
            t = raw / se if raw is not None and se and np.isfinite(raw) and np.isfinite(se) and se > 0 else None
            sgn = None if raw is None or not np.isfinite(raw) else int(np.sign(raw))
            pol.append({"ccy": c, "indicator": i, "category": v["category"], "n": v["n"],
                        "raw_beta": raw, "t": t, "sign_estimated": sgn, "catalog_polarity": int(v["direction"]),
                        "agree": None if sgn is None else sgn == int(v["direction"]),
                        "contracted": v["beta"]})
        for cat, vs in sorted(by.items()):
            con = [v["beta"] for v in vs]
            lo = [v["raw"] - 1.96 * v["se"] for v in vs if v["raw"] is not None and np.isfinite(v["raw"])]
            hi = [v["raw"] + 1.96 * v["se"] for v in vs if v["raw"] is not None and np.isfinite(v["raw"])]
            bet.append({"ccy": c, "category": cat, "indicators": len(vs), "mean_contracted_beta": float(np.mean(con)),
                        "raw_ci95_range": [float(min(lo)), float(max(hi))] if lo else None,
                        "mean_abs_contracted": float(np.mean(np.abs(con))), "n_days": m["n_days"],
                        "gamma_us": m["gamma_us"]})
    return bet, pol


def regime_by_day(days: pd.DatetimeIndex) -> pd.Series:
    glob, _ = schema.load("jpy")
    eng = Engine(glob, Inputs.load())
    cls = glob["classifiers"]["risk_regime_v0"]
    return pd.Series([classify(cls, eng.global_results(d))["regime"] for d in days], index=days)


def beta_fx_regimes(ri: ReactionInputs, start: str, end: pd.Timestamp) -> list[dict]:
    out = []
    days = regime_by_day(pd.date_range(start, end, freq="D"))
    for c in ["USD", "EUR", "GBP", "JPY", "CAD", "AUD"]:
        for vs in ("basket", "usd"):
            if c == "USD" and vs == "usd":
                continue
            f = ri.fx_frame(c, end + pd.Timedelta(days=2), vs)
            f = f[f.index >= start]
            reg = days.reindex(f.index)
            b, t, n = ols(f["rel"].to_numpy(), f["ret"].to_numpy())
            out.append({"ccy": c, "vs": vs, "regime": "all", "beta": b, "t": t, "n": n})
            for r in sorted(reg.unique()):
                m = (reg == r).to_numpy()
                b, t, n = ols(f["rel"].to_numpy()[m], f["ret"].to_numpy()[m])
                out.append({"ccy": c, "vs": vs, "regime": r, "beta": b, "t": t, "n": n})
    return out


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def wide(pn: pd.DataFrame, node: str, field: str = "z") -> pd.DataFrame:
    x = pn[(pn["node"] == node)]
    return x.pivot(index="asof", columns="ccy", values=field)


def first_cuts(pol: pd.Series, since: str) -> list[pd.Timestamp]:
    s = pol.dropna()
    ch = s.diff()
    ch = ch[ch.abs() > 1e-9]
    out = []
    for i, (d, v) in enumerate(ch.items()):
        if v >= 0 or d < pd.Timestamp(since):
            continue
        prev = ch.iloc[:i]
        if prev.empty or prev.iloc[-1] > 0 or (d - prev.index[-1]).days > 180:
            out.append(d)
    return out


def run(prereg: dict) -> dict:
    t0 = time.time()
    layer = schema.load_layer("reaction")
    ri = ReactionInputs.load()
    start = pd.Timestamp("2022-01-07")
    last = ri.bb.fx_levels().index[-1]
    fridays = pd.date_range(start, last, freq="7D")
    pn = panel(layer, ri, fridays)
    t_panel = time.time() - t0
    horizons = [1, 2, 4, 8, 13]
    uni = prereg["harness"]["universe_digest"]

    bas = ri.bb.basket_levels()
    fwd = {h: pd.DataFrame({c: forward(bas[c], fridays, h, 1e4) for c in CCYS}) for h in horizons}
    y2 = {c: ri.bb.by[f"Y2_{c}"] for c in uni}
    fwd_y2 = {h: pd.DataFrame({c: forward(y2[c], fridays, h, 100.0) for c in uni}) for h in (2, 4)}

    # baselines (as of t: data dated <= t - 1)
    carry = pd.DataFrame({c: [level_at(ri.bb.by[f"POL_{c}"], t - pd.Timedelta(days=1)) for t in fridays]
                          for c in CCYS}, index=fridays)
    mom = pd.DataFrame({c: [(level_at(bas[c], t - pd.Timedelta(days=1)) -
                             level_at(bas[c], t - pd.Timedelta(days=1 + 91))) * 1e4 for t in fridays]
                        for c in CCYS}, index=fridays)
    rnd = pd.DataFrame(np.random.default_rng(SEED).uniform(size=(len(fridays), len(CCYS))),
                       index=fridays, columns=CCYS)
    v3 = None
    if V3_SCORES.exists():
        v = pd.read_parquet(V3_SCORES)
        v = v[(v["variant"] == "A1") & (v["view"] == "ccy")]
        v["asof"] = pd.to_datetime(v["week"]).dt.normalize()
        v3 = v.pivot(index="asof", columns="unit", values="score").reindex(fridays)
    base = {"carry": carry, "momentum_13w": mom, "random": rnd}
    if v3 is not None:
        base["v3_A1"] = v3

    res: dict = {"panel_seconds": round(t_panel, 1), "fridays": [str(fridays[0].date()), str(fridays[-1].date())]}

    # H1
    dz = wide(pn, "digest_rates_4w")
    h1 = {}
    for h in (2, 4):
        F = fwd_y2[h]
        rows = []
        for c in uni:
            for t in fridays:
                rows.append((t, c, dz.at[t, c] if c in dz.columns else np.nan, F.at[t, c]))
        d = pd.DataFrame(rows, columns=["asof", "ccy", "s", "r"]).dropna()
        g = grid_mask(pd.DatetimeIndex(d["asof"]), start, h)
        ic, ic_no = spearman(d["s"], d["r"]), spearman(d["s"][g], d["r"][g])
        hv = []
        for lo, hi in halves(prereg):
            m = (d["asof"] >= lo) & (d["asof"] <= hi)
            hv.append(spearman(d["s"][m], d["r"][m]) if (m & g).sum() >= 10 else float("nan"))
        per = {c: spearman(d.loc[d.ccy == c, "s"], d.loc[d.ccy == c, "r"]) for c in uni}
        h1[h] = {"IC": ic, "IC_no": ic_no, "t": t_of_ic(ic_no, int(g.sum())), "N": int(len(d)),
                 "N_ind": int(g.sum()), "IC_h1": hv[0], "IC_h2": hv[1], "per_ccy": per,
                 "first_asof": str(d["asof"].min().date()) if len(d) else None}
    res["H1"] = h1

    # H2 + harness
    h2 = {}
    for sig in ("digest_fx_20d", "digest_fx_10d"):
        S = wide(pn, sig)
        h2[sig] = {}
        for h in horizons:
            R = fwd[h]
            row = {"tree": cs_metrics(S.reindex(columns=uni), R[uni], h, start, prereg)}
            for bn, B in base.items():
                row[f"{bn}@6"] = cs_metrics(B.reindex(columns=uni), R[uni], h, start, prereg)
                row[f"{bn}@8"] = cs_metrics(B.reindex(columns=CCYS), R[CCYS], h, start, prereg)
            h2[sig][h] = row
    res["H2"] = h2
    disc = {}
    for fl in ("gnbp_4w", "bngp_4w"):
        W = wide(pn, fl, "value")
        disc[fl] = {}
        for h in (2, 4):
            R = fwd[h]
            rows = [(W.at[t, c], R.at[t, c]) for t in fridays for c in CCYS if c in W.columns]
            d = pd.DataFrame(rows, columns=["f", "r"]).dropna()
            a, b = d.loc[d["f"] >= 2, "r"], d.loc[d["f"] == 0, "r"]
            se = np.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b)) if len(a) > 1 and len(b) > 1 else np.nan
            disc[fl][h] = {"mean_ge2": float(a.mean()) if len(a) else None, "n_ge2": int(len(a)),
                           "mean_0": float(b.mean()) if len(b) else None, "n_0": int(len(b)),
                           "diff": float(a.mean() - b.mean()) if len(a) and len(b) else None,
                           "t_welch": float((a.mean() - b.mean()) / se) if se and np.isfinite(se) else None}
    res["H2_discrete"] = disc

    # H3
    ev = ri.events_all
    ev = ev[ev["released"] & ev["fx_date"].notna()]
    rows = []
    for r in ev.itertuples(index=False):
        s = bas[r.ccy]
        k = s.index.searchsorted(r.fx_date)
        if k < len(s) and s.index[k] == r.fx_date and k + 5 < len(s):
            rows.append({"ccy": r.ccy, "z": r.z_signed, "date": r.release_dt.normalize(),
                         "drift": (s.iloc[k + 5] - s.iloc[k]) * 1e4})
    dr = pd.DataFrame(rows)
    h3 = {}
    for side, m in (("favourable_z>=1", dr["z"] >= 1), ("unfavourable_z<=-1 (sign flipped, descriptive)", dr["z"] <= -1)):
        x = dr[m].copy()
        if side.startswith("unf"):
            x["drift"] = -x["drift"]
        out = {}
        for grp, g in [("pooled", x)] + [(c, x[x["ccy"] == c]) for c in CCYS]:
            mean, t_net, n, G = clustered_mean_t(g["drift"].to_numpy(), g["date"].to_numpy(), mu0=6.0)
            out[grp] = {"mean_gross_bp": mean, "net_bp": mean - 6.0 if np.isfinite(mean) else None,
                        "t_net": t_net, "n": n, "clusters": G}
        h3[side] = out
    res["H3"] = h3

    # H4
    fs = wide(pn, "focus_shift_labor_3m", "value")
    h4 = {}
    for c in uni:
        cuts = first_cuts(ri.bb.by[f"POL_{c}"], "2023-06-01")
        if c not in fs.columns:
            continue
        s = fs[c].dropna()
        pre = pd.Series(False, index=s.index)
        for d in cuts:
            pre |= (s.index >= d - pd.Timedelta(days=84)) & (s.index < d)
        h4[c] = {"cuts": [str(d.date()) for d in cuts], "mean_pre": float(s[pre].mean()) if pre.any() else None,
                 "n_pre": int(pre.sum()), "mean_rest": float(s[~pre].mean()) if (~pre).any() else None,
                 "n_rest": int((~pre).sum())}
    allpre = [x for x in h4.values() if x["n_pre"]]
    res["H4"] = h4

    # descriptive
    snap = snapshot(layer, ri, last + pd.Timedelta(days=1))
    res["coverage"] = coverage_table(ri)
    res["events"] = events_table(ri.events_all)
    res["betas"], res["polarity"] = betas_table(snap)
    res["beta_rates_fx"] = beta_fx_regimes(ri, "2016-01-01", last)
    pn["year"] = pn["asof"].dt.year
    f = pn[pn["node"].isin(["gnbp_4w", "bngp_4w"]) & (pn["quality"] == "ok")]
    res["flags_by_year"] = (f.assign(ge1=f["value"] >= 1, ge2=f["value"] >= 2)
                            .groupby(["node", "year"]).agg(ccy_weeks=("value", "size"), ge1=("ge1", "sum"),
                                                           ge2=("ge2", "sum"), mean=("value", "mean"),
                                                           intensity=("intensity", "mean"))
                            .reset_index().to_dict(orient="records"))
    res["verdicts"] = verdicts(res, prereg)
    res["seconds"] = round(time.time() - t0, 1)
    return res


def verdicts(res: dict, prereg) -> dict:
    v = {}
    for h, m in res["H1"].items():
        c = {"IC>=0.05": m["IC"] >= 0.05 if np.isfinite(m["IC"]) else False,
             "t>=2": m["t"] >= 2 if np.isfinite(m["t"]) else False,
             "halves_same_sign": all(np.isfinite(x) and np.sign(x) == np.sign(m["IC"]) and x != 0
                                     for x in (m["IC_h1"], m["IC_h2"]))}
        v[f"H1@{h}w"] = {"criteria": c, "pass": all(c.values())}
    for sig, per in res["H2"].items():
        for h, row in per.items():
            if h not in (2, 4):
                continue
            m, mo = row["tree"], row["momentum_13w@6"]
            c = {"IC>=0.05": bool(np.isfinite(m["IC"]) and m["IC"] >= 0.05),
                 "t>=2": bool(np.isfinite(m["t"]) and m["t"] >= 2),
                 "halves_same_sign": all(np.isfinite(x) and np.sign(x) == np.sign(m["IC"]) and x != 0
                                         for x in (m["IC_h1"], m["IC_h2"])),
                 "IC-momentum>=0.03": bool(np.isfinite(m["IC"]) and np.isfinite(mo["IC"]) and m["IC"] - mo["IC"] >= 0.03)}
            v[f"H2 {sig}@{h}w"] = {"criteria": c, "pass": all(c.values())}
    for grp, m in res["H3"]["favourable_z>=1"].items():
        c = {"net>=5bp": bool(m["net_bp"] is not None and m["net_bp"] >= 5),
             "t_net>=2.5": bool(np.isfinite(m["t_net"]) and m["t_net"] >= 2.5)}
        v[f"H3 {grp}"] = {"criteria": c, "pass": all(c.values())}
    return v


def _clean(o):
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (float, np.floating)):
        return None if not np.isfinite(o) else round(float(o), 6)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, pd.Timestamp):
        return str(o.date())
    return o


def _f(x, nd=3):
    return "—" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x:.{nd}f}"


def markdown(r: dict) -> str:
    L = [f"# Reaction layer v0 — measurement (pre-registration {r['prereg_commit'][:8]}, attempt #2)", "",
         "Generated by scripts/measure/reaction_v0.py. Raw numbers; verdicts at the end.", "",
         "## H1 — digest_rates_4w z → forward 2Y change (pooled)", "",
         "| h | N | N_ind | IC | IC_no | t | IC 1st half | IC 2nd half | first asof |", "|---|---|---|---|---|---|---|---|---|"]
    for h, m in r["H1"].items():
        L.append(f"| {h} | {m['N']} | {m['N_ind']} | {_f(m['IC'])} | {_f(m['IC_no'])} | {_f(m['t'], 2)} | "
                 f"{_f(m['IC_h1'])} | {_f(m['IC_h2'])} | {m['first_asof']} |")
    L += ["", "## H2 — cross-sectional harness (forward basket return)", ""]
    for sig, per in r["H2"].items():
        L += [f"### {sig}", "", "| h | score | IC (grid) | t | N wk | N_ind | IC h1 | IC h2 | L/S bp | Sharpe | maxDD bp | pairs hit | coverage | accuracy |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for h in ["2", "4", "1", "8", "13"]:
            for name, m in per[h].items():
                L.append(f"| {h} | {name} | {_f(m['IC'])} | {_f(m['t'], 2)} | {m['N_weeks']} | {m['N_ind']} | "
                         f"{_f(m['IC_h1'])} | {_f(m['IC_h2'])} | {_f(m['ls_bp'], 1)} | {_f(m['ls_sharpe'], 2)} | "
                         f"{_f(m['ls_maxdd_bp'], 0)} | {_f(m['pairs_hit'])} | {_f(m['coverage'])} | {_f(m['accuracy'])} |")
        L.append("")
    L += ["### Discrete flags (pooled currency-weeks)", "", "| flag | h | mean ≥2 | N | mean 0 | N | diff | t |", "|---|---|---|---|---|---|---|---|"]
    for fl, per in r["H2_discrete"].items():
        for h, m in per.items():
            L.append(f"| {fl} | {h} | {_f(m['mean_ge2'], 1)} | {m['n_ge2']} | {_f(m['mean_0'], 1)} | {m['n_0']} | "
                     f"{_f(m['diff'], 1)} | {_f(m['t_welch'], 2)} |")
    L += ["", "## H3 — post-release drift, days +1..+5 vs basket", "", "| side | group | gross bp | net bp | t net | N | clusters |", "|---|---|---|---|---|---|---|"]
    for side, per in r["H3"].items():
        for g, m in per.items():
            L.append(f"| {side} | {g} | {_f(m['mean_gross_bp'], 1)} | {_f(m['net_bp'], 1)} | {_f(m['t_net'], 2)} | {m['n']} | {m['clusters']} |")
    L += ["", "## H4 — focus_shift_labor_3m before the first cut after a pause (descriptive)", "",
          "| ccy | cuts | mean pre | N pre | mean rest | N rest |", "|---|---|---|---|---|---|"]
    for c, m in r["H4"].items():
        L.append(f"| {c} | {', '.join(m['cuts']) or '—'} | {_f(m['mean_pre'])} | {m['n_pre']} | {_f(m['mean_rest'])} | {m['n_rest']} |")
    L += ["", "## Verdicts", ""]
    for k, v in r["verdicts"].items():
        failed = [c for c, ok in v["criteria"].items() if not ok]
        L.append(f"- {k}: {'PASS' if v['pass'] else 'fail'}" + (f" (fails: {', '.join(failed)})" if failed else ""))
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-json", type=Path, default=ROOT / "data" / "tree_runs" / "reaction_v0_metrics.json")
    ap.add_argument("--out-md", type=Path, default=ROOT / "docs" / "eval" / "reaction-v0.md")
    a = ap.parse_args()
    commit = prereg_commit()
    prereg = yaml.safe_load(PREREG.read_text())
    res = run(prereg)
    res["prereg_commit"] = commit
    res = _clean(res)
    a.out_json.write_text(json.dumps(res, indent=1, sort_keys=True) + "\n")
    a.out_md.write_text(markdown(res))
    print(f"prereg {commit}; {res['seconds']}s -> {a.out_json}, {a.out_md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
