"""Phase 5 measurement — attempt #5, the single test (scripts/measure/prereg_phase5.yml).

    python scripts/measure/phase5.py [--out-json data/tree_runs/phase5_metrics.json] [--out-md docs/eval/phase5.md]

Stage D: the gated tables (composite, labour at 1 month on the 15), then the descriptive ones.
Stage E: comparison with v3 on the 8 currencies, weekly, H.10, 2023-09 ->.
Refuses to run while the pre-registration has uncommitted changes."""
from __future__ import annotations

import argparse
import json
import sys
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "measure"))

import reaction_v0 as R  # noqa: E402
from src.tree.backbone import Backbone  # noqa: E402
from src.tree.composite import CCYS15, DEVELOPED, EMERGING, P5Inputs, composite_row  # noqa: E402

PREREG = ROOT / "scripts" / "measure" / "prereg_phase5.yml"
CCY8 = ["USD", "EUR", "GBP", "JPY", "CAD", "AUD", "NZD", "CHF"]
COST = {c: (2.0 if c in DEVELOPED else 6.0) for c in CCYS15}
SEED = 20261106
SUBS = [("2004-2009", "2004-01", "2009-12"), ("2010-2019", "2010-01", "2019-12"), ("2020-2026", "2020-01", "2026-12")]


# ---------------------------------------------------------------------------
# returns
# ---------------------------------------------------------------------------

def monthly_returns(inp: P5Inputs, panel: pd.DataFrame, k: int) -> pd.DataFrame:
    """Excess return vs the basket of the other available currencies, from the signal day of month t to
    the signal day of month t+k, bp. Carry = (rate_i - rate_USD) x k / 12, rates at t."""
    days = panel.drop_duplicates("month").set_index("month")["asof"].sort_index()
    months = list(days.index)
    fx = inp.fx
    out = {}
    for i, m in enumerate(months):
        if i + k >= len(months):
            continue
        t0, t1 = days[m], days[months[i + k]]
        mv = (fx.loc[t1] - fx.loc[t0]) * 1e4
        rates = pd.Series({c: inp.rate_at(c, t0) for c in CCYS15})
        ex = mv + (rates - rates["USD"]) * k / 12 * 100
        avail = ex.dropna()
        out[m] = pd.Series({c: avail[c] - avail.drop(c).mean() for c in avail.index})
    return pd.DataFrame(out).T.reindex(columns=CCYS15)


def weekly_returns_ecb(inp: P5Inputs, fridays: pd.DatetimeIndex, weeks: int) -> pd.DataFrame:
    fx = inp.fx
    out = {}
    for t in fridays:
        e = t + pd.Timedelta(weeks=weeks)
        if e > fx.index[-1]:
            continue
        a = fx.loc[:t].iloc[-1]
        b = fx.loc[:e].iloc[-1]
        mv = (b - a) * 1e4
        rates = pd.Series({c: inp.rate_at(c, t) for c in CCYS15})
        ex = (mv + (rates - rates["USD"]) * weeks / 52 * 100).dropna()
        out[t] = pd.Series({c: ex[c] - ex.drop(c).mean() for c in ex.index})
    return pd.DataFrame(out).T.reindex(columns=CCYS15)


# ---------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------

def ic_series(S: pd.DataFrame, Rt: pd.DataFrame, cols, min_n: int) -> pd.Series:
    S, Rt = S.reindex(columns=cols), Rt.reindex(columns=cols)
    out = {}
    for m in S.index.intersection(Rt.index):
        a, b = S.loc[m], Rt.loc[m]
        ok = a.notna() & b.notna()
        if ok.sum() >= min_n:
            out[m] = R.spearman(a[ok], b[ok])
    return pd.Series(out).dropna()


def tstat(x: pd.Series) -> dict:
    n = len(x)
    if n < 3:
        return {"IC": float(x.mean()) if n else float("nan"), "t": float("nan"), "N": n}
    return {"IC": float(x.mean()), "t": float(x.mean() / x.std(ddof=1) * np.sqrt(n)), "N": n}


def long_short(S: pd.DataFrame, Rt: pd.DataFrame, cols, k: int = 3, step: int = 1) -> dict:
    idx = [m for m in S.index.intersection(Rt.index)][::step]
    prev = pd.Series(0.0, index=cols)
    pnl = []
    for m in idx:
        s = S.loc[m, cols].dropna()
        r = Rt.loc[m, cols]
        s = s[r[s.index].notna()]
        if len(s) < 2 * k:
            continue
        w = pd.Series(0.0, index=cols)
        o = s.sort_values()
        w[o.index[-k:]] = 1.0 / k
        w[o.index[:k]] = -1.0 / k
        cost = float(((w - prev).abs() * pd.Series(COST)[cols]).sum())
        pnl.append(float((w * r.fillna(0)).sum()) - cost)
        prev = w
    x = pd.Series(pnl)
    cum = x.cumsum()
    per_year = 12 / step
    return {"mean_bp": float(x.mean()) if len(x) else np.nan,
            "sharpe": float(x.mean() / x.std(ddof=1) * np.sqrt(per_year)) if len(x) > 2 and x.std(ddof=1) > 0 else np.nan,
            "maxdd_bp": float((cum.cummax() - cum).max()) if len(x) else np.nan, "n": int(len(x))}


def pairs_hit(S: pd.DataFrame, Rt: pd.DataFrame, cols, step: int = 1) -> float:
    hit = tot = 0
    for m in list(S.index.intersection(Rt.index))[::step]:
        s, r = S.loc[m], Rt.loc[m]
        for a, b in combinations(cols, 2):
            if pd.notna(s.get(a)) and pd.notna(s.get(b)) and pd.notna(r.get(a)) and pd.notna(r.get(b)) \
                    and s[a] != s[b] and r[a] != r[b]:
                tot += 1
                hit += np.sign(s[a] - s[b]) == np.sign(r[a] - r[b])
    return hit / tot if tot else np.nan


def window(df: pd.DataFrame, a: str, b: str | None = None) -> pd.DataFrame:
    idx = pd.Index(df.index.astype(str))
    m = (idx >= a) & (idx <= (b or "9999"))
    return df[m]


def evaluate(S: pd.DataFrame, R1: pd.DataFrame, R3: pd.DataFrame, start="2004-01", end=None, cols=CCYS15,
             min_n: int = 10) -> dict:
    S1, Rw1, Rw3 = window(S, start, end), window(R1, start, end), window(R3, start, end)
    ic1 = ic_series(S1, Rw1, cols, min_n)
    res = {"1m": tstat(ic1)}
    offs = []
    for o in range(3):
        ics = ic_series(S1.iloc[o::3], Rw3, cols, min_n)
        offs.append(tstat(ics))
    res["3m_offsets"] = offs
    res["3m_mean_IC"] = float(np.nanmean([x["IC"] for x in offs]))
    res["3m_mean_t"] = float(np.nanmean([x["t"] for x in offs]))
    res["ls_1m"] = long_short(S1, Rw1, cols)
    res["pairs_hit_1m"] = pairs_hit(S1, Rw1, cols)
    res["subperiods"] = {n: tstat(ic1[(ic1.index.astype(str) >= a) & (ic1.index.astype(str) <= b)]) for n, a, b in SUBS}
    return res


def gate(name: str, res: dict, dm: dict) -> dict:
    c = {"IC>0 and t>=2.5": bool(res["1m"]["IC"] > 0 and res["1m"]["t"] >= 2.5),
         "IC>0 in 2004-09, 2010-19, 2020-26": all(res["subperiods"][n]["IC"] > 0 for n, _, _ in SUBS),
         "IC>0 on the 10 developed": bool(dm["1m"]["IC"] > 0),
         "IC>0 at 3 months": bool(res["3m_mean_IC"] > 0),
         "net L/S > 0": bool(res["ls_1m"]["mean_bp"] > 0)}
    return {"criteria": c, "pass": all(c.values())}


def wide(panel: pd.DataFrame, col: str, key: str = "month") -> pd.DataFrame:
    return panel.pivot(index=key, columns="ccy", values=col).reindex(columns=CCYS15)


# ---------------------------------------------------------------------------

def recompute(panel: pd.DataFrame, drop_carry: bool = False, carry_always: bool = False) -> pd.DataFrame:
    """Composite variants from the stored z (no re-standardisation)."""
    comps = ["labour", "confidence", "external", "tot"] + ([] if drop_carry else ["carry"])
    p = panel.copy()
    z = p[[f"z_{c}" for c in comps]].copy()
    if not drop_carry and not carry_always:
        z.loc[p["high_vol"], "z_carry"] = np.nan
    n = z.notna().sum(axis=1)
    p["variant"] = z.mean(axis=1, skipna=True).where(n >= 3)
    return p


def stage_e(candidate: str | None, inp: P5Inputs) -> dict:
    from src.tree.v4 import Phase4
    wk = pd.read_parquet(ROOT / "data" / "research" / "composite_panel_weekly.parquet")
    start = pd.Timestamp("2023-09-22")
    wk = wk[wk["asof"] >= start]
    fr = pd.DatetimeIndex(sorted(wk["asof"].unique()))
    lv = Backbone.load().basket_levels()

    def first_fix_after(t):                                   # Friday 21:00 UTC -> first H.10 fixing after
        i = lv.index.searchsorted(t + pd.Timedelta(days=1))
        return lv.index[i] if i < len(lv.index) else None

    fwd = {}
    for h in (2, 4):
        rows = {}
        for t in fr:
            a = first_fix_after(t)
            if a is None:
                continue
            j = lv.index.searchsorted(a + pd.Timedelta(weeks=h), side="right") - 1
            if lv.index[j] < a + pd.Timedelta(weeks=h) - pd.Timedelta(days=4) or a + pd.Timedelta(weeks=h) > lv.index[-1]:
                continue
            rows[t] = (lv.iloc[j] - lv.loc[a]) * 1e4
        fwd[h] = pd.DataFrame(rows).T.reindex(columns=CCY8)
    comp = wk.pivot(index="asof", columns="ccy", values="composite").reindex(columns=CCY8)
    lab = wk.pivot(index="asof", columns="ccy", values="z_labour").reindex(columns=CCY8)
    v3 = pd.read_parquet(ROOT / "data" / "v3_reconstruction.parquet")
    a1 = v3[v3["variant"] == "A1"].pivot(index="asof", columns="ccy", values="score").reindex(index=fr, columns=CCY8)
    b2 = v3[v3["variant"] == "B2"].pivot(index="asof", columns="ccy", values="score").reindex(index=fr, columns=CCY8)
    carry = wk.pivot(index="asof", columns="ccy", values="policy_rate").reindex(columns=CCY8)
    p4 = Phase4(fridays=fr)
    ms = p4.ms()
    zc = lambda df: df.apply(lambda r: (r - r.mean()) / r.std(ddof=1) if r.notna().sum() >= 3 else r * np.nan, axis=1)  # noqa: E731
    base_name = candidate or "composite"
    cand = comp if base_name == "composite" else lab
    live = (zc(cand) + zc(b2)) / 2
    scores = {"candidate_live": live, f"{base_name}_no_news": cand, "labour": lab, "v3_A1": a1, "carry": carry, "MS": ms}
    out = {"candidate": candidate, "base_used": base_name, "window": [str(fr[0].date()), str(fr[-1].date())], "ic": {}}
    for h in (2, 4):
        out["ic"][h] = {}
        for n, S in scores.items():
            ics = ic_series(S.reindex(index=fwd[h].index), fwd[h], CCY8, 6)
            g = ics[R.grid_mask(pd.DatetimeIndex(ics.index), fr[0], h)]
            out["ic"][h][n] = {**tstat(g), "IC_all_weeks": float(ics.mean()) if len(ics) else np.nan}
    if candidate:
        ok = all(out["ic"][h]["candidate_live"]["IC"] >= out["ic"][h]["v3_A1"]["IC"] - 0.02 for h in (2, 4))
        out["rule"] = {"candidate_becomes_direction": bool(ok)}
    else:
        out["rule"] = {"candidate_becomes_direction": False, "note": "stage D gave no candidate; descriptive only"}
    return out


def run() -> dict:
    inp = P5Inputs()
    panel = pd.read_parquet(ROOT / "data" / "research" / "composite_panel.parquet")
    R1, R3 = monthly_returns(inp, panel, 1), monthly_returns(inp, panel, 3)
    S = {"composite": wide(panel, "composite"), "labour": wide(panel, "z_labour")}
    res = {"gated": {}, "gates": {}, "descriptive": {}}
    for n in ("composite", "labour"):
        r = evaluate(S[n], R1, R3)
        dm = evaluate(S[n], R1, R3, cols=DEVELOPED, min_n=8)
        res["gated"][n] = {"all15": r, "developed": dm}
        res["gates"][f"{n} @1m (15)"] = gate(n, r, dm)
    passed = [n for n in ("composite", "labour") if res["gates"][f"{n} @1m (15)"]["pass"]]
    candidate = "composite" if "composite" in passed else ("labour" if "labour" in passed else None)
    res["decision"] = {"candidate": candidate}
    # descriptive
    D = res["descriptive"]
    for comp in ("excess_emp", "unemp_trend", "labour", "confidence", "external", "tot", "carry"):
        D[f"z_{comp}"] = evaluate(wide(panel, f"z_{comp}"), R1, R3)
    D["carry_with_vol_rule"] = evaluate(wide(panel.assign(x=panel["z_carry"].where(~panel["high_vol"])), "x"), R1, R3)
    D["composite_carry_always"] = evaluate(wide(recompute(panel, carry_always=True), "variant"), R1, R3)
    D["composite_without_carry"] = evaluate(wide(recompute(panel, drop_carry=True), "variant"), R1, R3)
    for dec, a, b in (("2000-2003 (descriptive window)", "2000-01", "2003-12"), ("2000s", "2000-01", "2009-12"),
                      ("2010s", "2010-01", "2019-12"), ("2020s", "2020-01", "2026-12")):
        D[f"composite {dec}"] = evaluate(S["composite"], R1, R3, start=a, end=b)
    D["composite developed"] = res["gated"]["composite"]["developed"]
    D["composite emerging"] = evaluate(S["composite"], R1, R3, cols=EMERGING, min_n=4)
    D["composite the 8"] = evaluate(S["composite"], R1, R3, cols=CCY8, min_n=7)
    per = {}
    W1 = window(S["composite"], "2004-01")
    for c in CCY8:
        j = pd.concat([W1[c], R1[c]], axis=1, join="inner").dropna()
        per[c] = {"ts_spearman": R.spearman(j.iloc[:, 0], j.iloc[:, 1]), "N": int(len(j)),
                  "hit_sign": float((np.sign(j.iloc[:, 0]) == np.sign(j.iloc[:, 1])).mean()) if len(j) else np.nan}
    D["composite per currency (time series)"] = per
    hv = panel.drop_duplicates("month").set_index("month")["high_vol"]
    ic1 = ic_series(window(S["composite"], "2004-01"), R1, CCYS15, 10)
    D["composite IC by vol"] = {"high": tstat(ic1[hv.reindex(ic1.index).fillna(False)]),
                                "low": tstat(ic1[~hv.reindex(ic1.index).fillna(False)])}
    icc = ic_series(window(wide(panel, "z_carry"), "2004-01"), R1, CCYS15, 10)
    D["carry IC by vol"] = {"high": tstat(icc[hv.reindex(icc.index).fillna(False)]),
                            "low": tstat(icc[~hv.reindex(icc.index).fillna(False)])}
    # weekly variant (ECB Friday), 2 and 4 weeks, 15 currencies
    wk = pd.read_parquet(ROOT / "data" / "research" / "composite_panel_weekly.parquet")
    Sw = wk.pivot(index="asof", columns="ccy", values="composite").reindex(columns=CCYS15)
    fr = pd.DatetimeIndex(Sw.index)
    D["weekly"] = {}
    for h in (2, 4):
        Rw = weekly_returns_ecb(inp, fr, h)
        ics = ic_series(Sw, Rw, CCYS15, 10)
        g = ics[R.grid_mask(pd.DatetimeIndex(ics.index), fr[0], h)]
        D["weekly"][h] = {**tstat(g), "window": [str(fr[0].date()), str(fr[-1].date())]}
    # baselines
    rnd = pd.DataFrame(np.random.default_rng(SEED).uniform(size=S["composite"].shape), index=S["composite"].index,
                       columns=CCYS15)
    D["baseline carry (rate level)"] = evaluate(wide(panel, "policy_rate"), R1, R3)
    D["baseline random"] = evaluate(rnd, R1, R3)
    from src.tree.v4 import Phase4
    days = panel.drop_duplicates("month").set_index("month")["asof"]
    p4 = Phase4(fridays=pd.date_range("2000-01-07", days.max(), freq="7D"))
    msl = p4.ms(lite=True)
    msm = pd.DataFrame({m: msl.loc[:t].iloc[-1] if len(msl.loc[:t]) else pd.Series(dtype=float) for m, t in days.items()}).T
    D["baseline MS-lite (8, COT + momentum)"] = evaluate(msm.reindex(columns=CCYS15), R1, R3, cols=CCY8, min_n=7)
    D["composite on the 8 (same window as MS-lite)"] = D["composite the 8"]
    res["E"] = stage_e(candidate, inp)
    return res


def _f(x, nd=3):
    return "—" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x:.{nd}f}"


def markdown(r: dict) -> str:
    L = [f"# Phase 5 — the single test (pre-registration {r['prereg_commit'][:8]}, attempt #5)", "",
         "## Gated (1 month, 15 currencies, 2004-01 ->)", "",
         "| test | IC | t | N | 2004-09 | 2010-19 | 2020-26 | developed IC (t) | 3m IC (mean) | net L/S bp | Sharpe | pairs hit |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for n, v in r["gated"].items():
        a, d = v["all15"], v["developed"]
        sp = a["subperiods"]
        L.append(f"| {n} | {_f(a['1m']['IC'])} | {_f(a['1m']['t'], 2)} | {a['1m']['N']} | "
                 + " | ".join(f"{_f(sp[k]['IC'])}" for k, _, _ in SUBS) +
                 f" | {_f(d['1m']['IC'])} ({_f(d['1m']['t'], 2)}) | {_f(a['3m_mean_IC'])} | {_f(a['ls_1m']['mean_bp'], 1)} | "
                 f"{_f(a['ls_1m']['sharpe'], 2)} | {_f(a['pairs_hit_1m'])} |")
    L += ["", "Gates:", ""]
    for k, v in r["gates"].items():
        failed = [c for c, ok in v["criteria"].items() if not ok]
        L.append(f"- {k}: {'PASS' if v['pass'] else 'fail'}" + (f" (fails: {', '.join(failed)})" if failed else ""))
    L += ["", f"Decision: candidate = {r['decision']['candidate']}", "", "## Descriptive", "",
          "| series | IC 1m | t | N | 3m IC | net L/S bp | Sharpe |", "|---|---|---|---|---|---|---|"]
    for n, v in r["descriptive"].items():
        if isinstance(v, dict) and "1m" in v:
            L.append(f"| {n} | {_f(v['1m']['IC'])} | {_f(v['1m']['t'], 2)} | {v['1m']['N']} | {_f(v['3m_mean_IC'])} | "
                     f"{_f(v['ls_1m']['mean_bp'], 1)} | {_f(v['ls_1m']['sharpe'], 2)} |")
    L += ["", "```", json.dumps({k: r["descriptive"][k] for k in ("composite per currency (time series)", "composite IC by vol",
                                                                   "carry IC by vol", "weekly")}, indent=1), "```", "",
          "## Stage E (8 currencies, weekly, H.10)", "", "| h | score | IC | t | N |", "|---|---|---|---|---|"]
    for h, per in r["E"]["ic"].items():
        for n, m in per.items():
            L.append(f"| {h} | {n} | {_f(m['IC'])} | {_f(m['t'], 2)} | {m['N']} |")
    L += ["", f"Rule: {json.dumps(r['E']['rule'])}", ""]
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-json", type=Path, default=ROOT / "data" / "tree_runs" / "phase5_metrics.json")
    ap.add_argument("--out-md", type=Path, default=ROOT / "docs" / "eval" / "phase5.md")
    a = ap.parse_args()
    commit = R.prereg_commit(PREREG)
    res = run()
    res["prereg_commit"] = commit
    out = R._clean(res)
    a.out_json.write_text(json.dumps(out, indent=1, sort_keys=True, default=str) + "\n")
    a.out_md.write_text(markdown(out))
    print(f"prereg {commit} -> {a.out_json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
